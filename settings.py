"""Offline settings validation and atomic persistence."""
import json
import math
import os
from pathlib import Path
import re
import tempfile


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate config property: {key}')
        result[key] = value
    return result


def finite_number(value, name):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a finite number')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'{name} must be a finite number') from None
    if not math.isfinite(number):
        raise ValueError(f'{name} must be a finite number')
    return number


def coordinates(lat, lon):
    lat, lon = finite_number(lat, 'latitude'), finite_number(lon, 'longitude')
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('coordinates out of range')
    return lat, lon


def host(value):
    # Current HTTP URL construction and UDP transport support IPv4/hostnames.
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value):
        raise ValueError('server must be an IPv4 address or hostname without a URL or port')
    return value


def boolean(value, name):
    if type(value) is not bool:
        raise ValueError(f'{name} must be a boolean')
    return value


def validate_config(cfg):
    if not isinstance(cfg, dict):
        raise ValueError('settings must be an object')
    if cfg.keys() - {'kraken_servers', 'tak_server_ip', 'tak_server_port', 'tak_multicast_state'}:
        raise ValueError('unknown config settings; retain a backup before migrating them')
    servers = cfg.get('kraken_servers')
    if not isinstance(servers, list):
        raise ValueError('kraken_servers must be an array')
    normalized = []
    stations = set()
    for server in servers:
        if not isinstance(server, dict):
            raise ValueError('each server must be an object')
        allowed = {'ip', 'station', 'start_angle', 'end_angle', 'persist_uid_line', 'line_distance'}
        if server.keys() - allowed:
            raise ValueError('unknown server settings')
        station = server.get('station', '1')
        if (not isinstance(station, str) or not station.strip()
                or any(not (0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in station)):
            raise ValueError('station must be a non-empty XML-compatible string')
        if station in stations:
            raise ValueError('station IDs must be unique')
        stations.add(station)
        start, end = server.get('start_angle'), server.get('end_angle')
        if (start is None) != (end is None):
            raise ValueError('both exclusion angles must be set or both cleared')
        if start is not None:
            start = finite_number(start, 'start_angle') % 360
            end = finite_number(end, 'end_angle') % 360
        distance = finite_number(server.get('line_distance', 6), 'line_distance')
        if distance <= 0:
            raise ValueError('line_distance must be positive')
        normalized.append({
            'ip': host(server.get('ip', '0.0.0.0')), 'station': station,
            'start_angle': start, 'end_angle': end,
            'persist_uid_line': boolean(server.get('persist_uid_line', False), 'persist_uid_line'),
            'line_distance': distance,
        })
    port = cfg.get('tak_server_port')
    if type(port) is str and port.isascii() and port.isdigit():
        port = int(port)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('tak_server_port must be an integer from 1 to 65535')
    return {
        'kraken_servers': normalized,
        'tak_server_ip': host(cfg.get('tak_server_ip')),
        'tak_server_port': str(port),
        'tak_multicast_state': boolean(cfg.get('tak_multicast_state'), 'tak_multicast_state'),
    }


def atomic_save(path, cfg):
    """Replace only after a complete, flushed JSON write; leave old data on failure."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as target:
            temporary = target.name
            json.dump(cfg, target, indent=2, allow_nan=False)
            target.write('\n')
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
