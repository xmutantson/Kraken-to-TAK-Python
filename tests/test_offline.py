"""Offline regressions. Real socket, HTTP and GPS access fail closed in every test."""
import copy
from datetime import datetime
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid
import xml.etree.ElementTree as ET

import KrakenToTAK as bridge
import settings


def sample_parts(lat='12.5', lon='34.5', bearing='359.5'):
    return ['0', bearing, '0', '0', '0', '0', '0', '0', lat, lon]


class OfflineTest(unittest.TestCase):
    gps_get_current = staticmethod(bridge.gpsd.get_current)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for target in ('socket.socket', 'requests.sessions.Session.request',
                       'gpsd.connect', 'gpsd.get_current'):
            # BaseException avoids being swallowed by the application's fallback handlers.
            guard = patch(target, side_effect=UnexpectedLiveIO('unexpected live I/O: ' + target))
            guard.start()
            self.addCleanup(guard.stop)
        config_patch = patch.object(bridge, 'CONFIG_FILE', str(Path(self.directory.name) / 'config.json'))
        config_patch.start()
        self.addCleanup(config_patch.stop)
        bridge.apply_config({'kraken_servers': [bridge._default_server()],
                             'tak_server_ip': '0.0.0.0', 'tak_server_port': '6666',
                             'tak_multicast_state': True})
        bridge._gpsd_connected = False
        bridge.server_statuses = []
        bridge.packets_sent = 0
        bridge.last_packet_time = None
        bridge.app.config['TESTING'] = True
        self.client = bridge.app.test_client()


class UnexpectedLiveIO(BaseException):
    pass


class GeometryTests(OfflineTest):
    def test_zero_distance_and_cardinal_direction(self):
        self.assertEqual(bridge.calculate_second_point(0, 0, 0, 0), (0, 0))
        lat, lon = bridge.calculate_second_point(0, 0, 0, 6)
        self.assertAlmostEqual(lat, 0.053959296, places=8)
        self.assertAlmostEqual(lon, 0)

    def test_antimeridian_both_directions(self):
        for lon, bearing, sign in ((179.99, 90, -1), (-179.99, 270, 1)):
            lat2, lon2 = bridge.calculate_second_point(0, lon, bearing, 6)
            self.assertAlmostEqual(lat2, 0)
            self.assertTrue(-180 <= lon2 < 180)
            self.assertGreater(lon2 * sign, 179)

    def test_polar_result_remains_valid(self):
        settings.coordinates(*bridge.calculate_second_point(89.9999, 45, 0, 6))

    def test_invalid_geometry(self):
        for args in ((91, 0, 0, 6), (0, 181, 0, 6), (0, 0, 'nan', 6),
                     (0, 0, 0, float('inf')), (0, 0, 0, -1)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                bridge.calculate_second_point(*args)

    def test_wraparound_exclusion_and_boundaries(self):
        for angle in (350, 359.9, 0, 10, 370, -10):
            self.assertFalse(bridge.evaluate_angle_range(350, 10, angle))
        for angle in (10.1, 180, 349.9):
            self.assertTrue(bridge.evaluate_angle_range(350, 10, angle))

    def test_fractional_angles_are_not_truncated(self):
        self.assertTrue(bridge.evaluate_angle_range(10.8, 20.2, 10.5))
        self.assertTrue(bridge.evaluate_angle_range(10.8, 20.2, 20.5))
        self.assertFalse(bridge.evaluate_angle_range(10.8, 20.2, 10.9))

    def test_disabled_and_equal_exclusion(self):
        self.assertTrue(bridge.evaluate_angle_range(None, None, 42))
        self.assertFalse(bridge.evaluate_angle_range(10, 10, 10))
        self.assertTrue(bridge.evaluate_angle_range(10, 10, 10.1))


class DataTests(OfflineTest):
    def test_parse_complete_sample_and_normalize_bearing(self):
        self.assertEqual(bridge.parse_kraken_data(sample_parts(bearing='-0.5')), (12.5, 34.5, 359.5))

    def test_reject_missing_or_invalid_sample(self):
        for parts in (None, [], [''], sample_parts(lat='nan'), sample_parts(lon='inf'),
                      sample_parts(lat='91'), sample_parts(bearing=''), sample_parts(bearing='NaN')):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                bridge.parse_kraken_data(parts)

    def test_http_timeout_is_bounded(self):
        with patch.object(bridge.requests, 'get', side_effect=bridge.requests.Timeout) as get:
            self.assertIsNone(bridge.fetch_kraken_data('example.invalid'))
        get.assert_called_once_with('http://example.invalid:8081/DOA_value.html', timeout=5)

    def test_http_error_body_is_not_data(self):
        response = Mock(text=','.join(sample_parts()))
        response.raise_for_status.side_effect = bridge.requests.HTTPError('unavailable')
        with patch.object(bridge.requests, 'get', return_value=response):
            self.assertIsNone(bridge.fetch_kraken_data('example.invalid'))

    def test_http_success(self):
        response = Mock(text=','.join(sample_parts()))
        with patch.object(bridge.requests, 'get', return_value=response):
            self.assertEqual(bridge.fetch_kraken_data('example.invalid'), sample_parts())
        response.raise_for_status.assert_called_once_with()

    def test_gps_no_fix_uses_kraken_instead_of_zero_location(self):
        with patch.object(bridge.gpsd, 'connect'), patch.object(bridge.gpsd, 'get_current',
                return_value=SimpleNamespace(mode=1, lat=0, lon=0)):
            self.assertEqual(bridge.get_gps_data(sample_parts()), (12.5, 34.5))

    def test_valid_zero_gps_fix_and_connection_reuse(self):
        with patch.object(bridge.gpsd, 'connect') as connect, patch.object(bridge.gpsd, 'get_current',
                return_value=SimpleNamespace(mode=2, lat=0, lon=0)):
            self.assertEqual(bridge.get_gps_data(sample_parts()), (0, 0))
            bridge.get_gps_data()
        connect.assert_called_once_with()

    def test_invalid_gps_fix_falls_back(self):
        with patch.object(bridge.gpsd, 'connect'), patch.object(bridge.gpsd, 'get_current',
                return_value=SimpleNamespace(mode=3, lat=float('nan'), lon=0)):
            self.assertEqual(bridge.get_gps_data(sample_parts()), (12.5, 34.5))

    def test_real_decoder_preserves_missing_coordinate_presence(self):
        for tpv in ({'mode': 2, 'lat': 51.0}, {'mode': 3, 'lon': 0},
                    {'mode': 2}, {'mode': 3, 'lat': None, 'lon': 1}):
            with self.subTest(tpv=tpv):
                raw = {'active': 1, 'tpv': [tpv], 'sky': [{}]}
                original = copy.deepcopy(raw)
                packet = bridge.gpsd.GpsResponse.from_json(raw)
                self.assertEqual(raw, original)
                with patch.object(bridge.gpsd, 'connect'), patch.object(
                        bridge.gpsd, 'get_current', return_value=packet):
                    self.assertEqual(bridge.get_gps_data(sample_parts()), (12.5, 34.5))
                    self.assertEqual(bridge.get_gps_data(), (None, None))

    def test_real_decoder_accepts_explicit_zero_coordinates(self):
        for lat, lon in ((0, 0), (51, 0), (0, -73), (-90, 180)):
            with self.subTest(lat=lat, lon=lon):
                packet = bridge.gpsd.GpsResponse.from_json({
                    'active': 1, 'tpv': [{'mode': 2, 'lat': lat, 'lon': lon}], 'sky': [{}]})
                with patch.object(bridge.gpsd, 'connect'), patch.object(
                        bridge.gpsd, 'get_current', return_value=packet):
                    self.assertEqual(bridge.get_gps_data(sample_parts()), (lat, lon))

    def test_real_decoder_uses_last_tpv_without_carrying_old_coordinates(self):
        raw = {'active': 1, 'tpv': [{'mode': 3, 'lat': 1, 'lon': 2},
                                 {'mode': 2, 'lat': 51}], 'sky': [{}]}
        packet = bridge.gpsd.GpsResponse.from_json(raw)
        self.assertEqual(packet.lat, 51)
        self.assertIsNone(packet.lon)

    def test_installed_poll_decoder_with_in_memory_stream(self):
        # Restore the real get_current under the live-I/O guard, but supply only
        # an in-memory stream. This exercises the dependency's actual decoder lookup.
        raw = {'class': 'POLL', 'active': 1,
               'tpv': [{'mode': 2, 'lat': 51}], 'sky': [{}]}
        stream = Mock()
        stream.readline.return_value = json.dumps(raw)
        original = self.gps_get_current
        with patch.object(bridge.gpsd, 'gpsd_stream', stream), patch.object(
                bridge.gpsd, 'get_current', original), patch.object(bridge.gpsd, 'connect'):
            self.assertEqual(bridge.get_gps_data(sample_parts()), (12.5, 34.5))
        stream.write.assert_called_once_with('?POLL;\n')

    def test_partial_real_packet_poll_emits_fallback_coordinates(self):
        packet = bridge.gpsd.GpsResponse.from_json({
            'active': 1, 'tpv': [{'mode': 2, 'lat': 51}], 'sky': [{}]})
        with patch.object(bridge.gpsd, 'connect'), patch.object(
                bridge.gpsd, 'get_current', return_value=packet), patch.object(
                bridge, 'fetch_kraken_data', return_value=sample_parts()), patch.object(
                bridge, 'send_payload') as send:
            bridge.poll_once()
        point = ET.fromstring(send.call_args_list[0].args[0]).find('point')
        self.assertEqual((float(point.get('lat')), float(point.get('lon'))), (12.5, 34.5))

    def test_missing_gps_and_bad_fallback(self):
        with patch.object(bridge.gpsd, 'connect', side_effect=OSError('offline')):
            self.assertEqual(bridge.get_gps_data(['bad']), (None, None))
        self.assertFalse(bridge._gpsd_connected)


class XmlTests(OfflineTest):
    def test_point_attribute_text_and_times_round_trip(self):
        label = 'Station A & B "survey"'
        payload = bridge.create_cot_xml_payload_point(12, 34, label, '', '', 'SignalMedic',
            'Yellow', 'Team Member', 'GPS', '', '', '', '', '', '', '0', '', label)
        root = ET.fromstring(payload)
        self.assertEqual(root.attrib['uid'], label + '-SignalMedic')
        self.assertEqual(root.find('detail/contact').attrib['callsign'], label)
        self.assertEqual(root.attrib['time'], root.attrib['start'])
        start = datetime.fromisoformat(root.attrib['start'])
        stale = datetime.fromisoformat(root.attrib['stale'])
        self.assertEqual((stale - start).total_seconds(), 75)

    def test_line_uses_arguments_and_preserves_label(self):
        label = "O'Brien & survey"
        root = ET.fromstring(bridge.create_cot_xml_payload_line(12, 34, (13, 35), label))
        self.assertEqual(root.attrib['uid'], label)
        links = root.findall('detail/link')
        self.assertEqual(links[0].attrib['point'], '12.0,34.0')
        self.assertEqual(links[1].attrib['point'], '13.0,35.0')

    def test_persistent_line_uses_uuid(self):
        uid = bridge.generate_uid_line('station')
        self.assertEqual(uuid.UUID(uid.removeprefix('station-DOA-to-TAK-')).version, 4)


class ConfigTests(OfflineTest):
    def test_successful_save_load_round_trip(self):
        servers = [dict(bridge._default_server('example.invalid', 'A & B'), line_distance=2.5)]
        response = self.client.post('/update_settings', json={'kraken_servers': servers,
                                    'tak_server_port': 7778, 'takMulticast': False})
        self.assertEqual(response.status_code, 200)
        saved = json.loads(Path(bridge.CONFIG_FILE).read_text())
        self.assertEqual(saved, bridge.current_config())
        self.assertFalse(saved['tak_multicast_state'])
        bridge.kraken_servers = []
        self.assertTrue(bridge.load_config())
        self.assertEqual(bridge.kraken_servers, servers)

    def test_invalid_update_keeps_all_memory_and_disk(self):
        bridge.save_config()
        original = copy.deepcopy(bridge.current_config())
        before = Path(bridge.CONFIG_FILE).read_bytes()
        response = self.client.post('/update_settings', json={'tak_server_ip': 'example.invalid', 'tak_server_port': 0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(bridge.current_config(), original)
        self.assertEqual(Path(bridge.CONFIG_FILE).read_bytes(), before)

    def test_write_failure_returns_error_without_changing_memory_or_disk(self):
        bridge.save_config()
        original = copy.deepcopy(bridge.current_config())
        before = Path(bridge.CONFIG_FILE).read_bytes()
        with patch('settings.os.replace', side_effect=OSError('synthetic disk failure')):
            response = self.client.post('/update_settings', json={'tak_server_port': 7778})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(bridge.current_config(), original)
        self.assertEqual(Path(bridge.CONFIG_FILE).read_bytes(), before)
        self.assertEqual(list(Path(self.directory.name).glob('*.tmp')), [])

    def test_serialization_failure_preserves_old_file(self):
        bridge.save_config()
        before = Path(bridge.CONFIG_FILE).read_bytes()
        with self.assertRaises(ValueError):
            settings.atomic_save(bridge.CONFIG_FILE, {'value': float('nan')})
        self.assertEqual(Path(bridge.CONFIG_FILE).read_bytes(), before)
        self.assertEqual(list(Path(self.directory.name).glob('*.tmp')), [])

    def test_invalid_json_shapes(self):
        for value in (None, [], 'text', 3):
            with self.subTest(value=value):
                self.assertEqual(self.client.post('/update_settings', json=value).status_code, 400)
        self.assertEqual(self.client.post('/update_settings', data='{', content_type='application/json').status_code, 400)

    def test_invalid_settings(self):
        for patch_data in ({'takMulticast': 'false'}, {'tak_server_port': True},
                           {'tak_server_port': 1.5}, {'tak_server_port': 65536},
                           {'tak_server_ip': 'http://example.invalid/path'}, {'unknown': 1},
                           {'kraken_servers': None}, {'kraken_servers': [None]}):
            with self.subTest(data=patch_data):
                self.assertEqual(self.client.post('/update_settings', json=patch_data).status_code, 400)

    def test_invalid_per_server_settings(self):
        for values in ({'line_distance': 0}, {'line_distance': 'nan'}, {'line_distance': True},
                       {'start_angle': 10}, {'start_angle': 'inf', 'end_angle': 20},
                       {'persist_uid_line': 'false'}, {'station': ''}, {'station': 'A\x00B'},
                       {'ip': None}, {'extra': True}):
            server = dict(bridge._default_server(), **values)
            with self.subTest(values=values):
                self.assertEqual(self.client.post('/update_settings', json={'kraken_servers': [server]}).status_code, 400)

    def test_duplicate_station_ids_rejected(self):
        servers = [bridge._default_server('one.invalid'), bridge._default_server('two.invalid')]
        self.assertEqual(self.client.post('/update_settings', json={'kraken_servers': servers}).status_code, 400)

    def test_empty_server_list_preserved(self):
        self.assertEqual(self.client.post('/update_settings', json={'kraken_servers': []}).status_code, 200)
        self.assertEqual(bridge.kraken_servers, [])

    def test_legacy_migration_preserves_choices_without_rewriting_file(self):
        legacy = {'kraken_server': 'example.invalid', 'kraken_station': 'custom',
                  'start_angle': 350.5, 'end_angle': 10.5, 'line_distance': 2,
                  'persist_uid_line': True, 'tak_multicast_state': False, 'tak_server_port': '7778'}
        path = Path(bridge.CONFIG_FILE)
        path.write_text(json.dumps(legacy))
        before = path.read_bytes()
        self.assertTrue(bridge.load_config())
        server = bridge.kraken_servers[0]
        self.assertEqual((server['station'], server['start_angle'], server['line_distance']), ('custom', 350.5, 2))
        self.assertTrue(server['persist_uid_line'])
        self.assertFalse(bridge.tak_multicast_state)
        self.assertEqual(path.read_bytes(), before)

    def test_corrupt_config_does_not_partially_apply(self):
        before = copy.deepcopy(bridge.current_config())
        path = Path(bridge.CONFIG_FILE)
        for text in ('{', 'null', '{"kraken_servers": [], "tak_server_port": 0}'):
            path.write_text(text)
            self.assertFalse(bridge.load_config())
            self.assertEqual(bridge.current_config(), before)
            self.assertEqual(path.read_text(), text)

    def test_missing_config_retains_defaults(self):
        self.assertTrue(bridge.load_config())
        self.assertFalse(Path(bridge.CONFIG_FILE).exists())

    def test_ambiguous_or_unknown_config_is_preserved_for_manual_repair(self):
        path = Path(bridge.CONFIG_FILE)
        for text in ('{"tak_server_port": "6666", "tak_server_port": "7778"}',
                     '{"custom_future_setting": true}'):
            path.write_text(text)
            self.assertFalse(bridge.load_config())
            self.assertEqual(path.read_text(), text)

    def test_status_associates_identity_after_removal_or_reorder(self):
        bridge.kraken_servers = [bridge._default_server('two.invalid', 'two'),
                                 bridge._default_server('new.invalid', 'new')]
        bridge.server_statuses = [{'ip': 'one.invalid', 'station': 'one', 'lat': 11, 'reachable': True},
                                  {'ip': 'two.invalid', 'station': 'two', 'lat': 22, 'reachable': True}]
        servers = self.client.get('/status').get_json()['servers']
        self.assertEqual(servers[0]['lat'], 22)
        self.assertIsNone(servers[1]['lat'])
        self.assertFalse(servers[1]['reachable'])

    def test_dashboard_renders_without_network(self):
        self.assertEqual(self.client.get('/').status_code, 200)


class SendTests(OfflineTest):
    def test_failed_local_send_does_not_count(self):
        with patch.object(bridge, 'send_cot_payload', return_value=False), patch.object(bridge, 'send_to_multicast', return_value=False):
            self.assertFalse(bridge.send_payload('synthetic', 'example.invalid', 6666, True))
        self.assertEqual(bridge.packets_sent, 0)
        self.assertIsNone(bridge.last_packet_time)

    def test_multicast_success_counts_event_once(self):
        with patch.object(bridge, 'send_cot_payload', return_value=False), patch.object(bridge, 'send_to_multicast', return_value=True):
            self.assertTrue(bridge.send_payload('synthetic', 'example.invalid', 6666, True))
        self.assertEqual(bridge.packets_sent, 1)
        self.assertIsNotNone(bridge.last_packet_time)

    def test_disabled_multicast_is_not_called(self):
        with patch.object(bridge, 'send_cot_payload', return_value=True), patch.object(bridge, 'send_to_multicast') as multicast:
            bridge.send_payload('synthetic', 'example.invalid', 6666, False)
        multicast.assert_not_called()

    def test_socket_error_returns_false_without_transmission(self):
        with patch.object(bridge.socket, 'socket', side_effect=socket.error('synthetic failure')):
            self.assertFalse(bridge.send_cot_payload('synthetic', 'example.invalid', 6666))
            self.assertFalse(bridge.send_to_multicast('synthetic'))


class PollingTests(OfflineTest):
    def test_unreachable_server_does_not_stop_following_station(self):
        bridge.kraken_servers = [bridge._default_server('one.invalid', 'one'),
                                 bridge._default_server('two.invalid', 'two')]
        with patch.object(bridge, 'fetch_kraken_data', side_effect=[None, sample_parts()]), \
             patch.object(bridge.gpsd, 'connect', side_effect=OSError('offline')), \
             patch.object(bridge, 'send_payload') as send:
            bridge.poll_once()
        self.assertEqual(send.call_count, 2)
        self.assertFalse(bridge.server_statuses[0]['reachable'])
        self.assertEqual(bridge.server_statuses[1]['lat'], 12.5)
        roots = [ET.fromstring(c.args[0]) for c in send.call_args_list]
        self.assertEqual([root.attrib['uid'] for root in roots], ['two-SignalMedic', 'two-DOA-to-TAK'])

    def test_missing_or_nonfinite_data_does_not_generate_payloads(self):
        for parts in ([], sample_parts(lat='NaN'), sample_parts(lon='181')):
            with self.subTest(parts=parts), patch.object(bridge, 'fetch_kraken_data', return_value=parts), \
                 patch.object(bridge.gpsd, 'connect', side_effect=OSError('offline')), \
                 patch.object(bridge, 'send_payload') as send:
                bridge.poll_once()
                send.assert_not_called()

    def test_exclusion_suppresses_only_line_and_preserves_multicast_choice(self):
        bridge.kraken_servers = [dict(bridge._default_server(), start_angle=350, end_angle=10)]
        bridge.tak_multicast_state = False
        with patch.object(bridge, 'fetch_kraken_data', return_value=sample_parts()), \
             patch.object(bridge.gpsd, 'connect', side_effect=OSError('offline')), \
             patch.object(bridge, 'send_payload') as send:
            bridge.poll_once()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(ET.fromstring(send.call_args.args[0]).attrib['type'], 'a-f-G-U-C')
        self.assertFalse(send.call_args.args[3])

    def test_poll_uses_one_settings_snapshot(self):
        def fetch(_server):
            bridge.tak_server_ip = 'new.invalid'
            bridge.kraken_servers = []
            return sample_parts()
        with patch.object(bridge, 'fetch_kraken_data', side_effect=fetch), \
             patch.object(bridge.gpsd, 'connect', side_effect=OSError('offline')), \
             patch.object(bridge, 'send_payload') as send:
            bridge.poll_once()
        self.assertEqual(send.call_count, 2)
        self.assertTrue(all(c.args[1] == '0.0.0.0' for c in send.call_args_list))


if __name__ == '__main__':
    unittest.main()
