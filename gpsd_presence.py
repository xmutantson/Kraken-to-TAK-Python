"""Preserve missing coordinates at gpsd-py3's lossy decoding boundary.

The client resolves its module-level GpsResponse class on each poll. Install a
subclass at that extension point so transport/connect behavior stays unchanged.
No installed dependency files are modified.
"""
import gpsd


class PresenceAwareGpsResponse(gpsd.GpsResponse):
    @classmethod
    def from_json(cls, packet):
        # Capture presence in the selected (last) TPV before the upstream decoder
        # replaces absent values with 0.0. Explicit zero is a valid coordinate.
        tpv = packet['tpv'][-1]
        missing = {field for field in ('lat', 'lon') if field not in tpv}
        result = super().from_json(packet)
        for field in missing:
            setattr(result, field, None)
        return result


# gpsd-py3 exposes no decoder injection argument; replace only its response class.
gpsd.GpsResponse = PresenceAwareGpsResponse
