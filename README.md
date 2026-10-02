KrakenSDR Raspberry Pi 4 image quickstart (this image requires a couple commands to set the environment up)
https://www.youtube.com/watch?v=3kVVMPc3WTc

Example
https://www.youtube.com/watch?v=AjJOk-vhxMA

A Python application with html dashboard designed to help use your KrakenSDR with TAK products.
For use in a non-production environment.
Defaults are set to be run on the same server as your KrakenSDR server, however other network configurations should work.

## Dependencies and offline validation

Use an isolated virtual environment and install `requirements.txt`. To test
without starting the bridge, web server, GPS connection, or TAK transmission:

```
python -m unittest discover -s tests -v
node --test tests/dashboard.test.cjs
```

Python tests require the declared application dependencies. Dashboard tests use
Node's built-in test runner and need no npm packages. Tests use synthetic data,
temporary config files, mocked transports, and guards against real sockets.

Settings are validated and saved atomically before becoming active. A failed save
returns an error and retains the previous settings. Startup refuses corrupt or
unsupported config instead of silently running with defaults. Keep a backup when
migrating an existing `config.json`; valid legacy single-server settings load
without rewriting the file, and the next successful save uses the multi-server
format. Station IDs must be unique; exclusion angles are either both set or both
cleared. Multicast and line persistence choices are retained.

The dashboard packet count means logical events accepted by at least one local
UDP send, not delivery acknowledgements from a TAK client. See
[implementation notes](IMPLEMENTATION_NOTES.md) for upstream provenance, remaining
GPS limitations, and checks intentionally not performed.

Tested using Python and Python 3 with a Chrome browser.
# Quick Start
Step 1:
Clone or download Kraken-to-TAK-Python to your computer

Step 2:
Initialize or start your KrakenSDR software (this can be done after starting Kraken to TAK also)

Step 3:
Navigate to the directory and start
```
$ cd Kraken-to-TAK-Python
$ python KrakenToTAK.py
```

Step 4:
Open a browser and navigate to http://<YOUR_IP:8000

# Instructions
Kraken Server
	-Kraken Server IP should be the IPv4 of the computer that your KrakenSDR software is running on. The Kraken Station ID is for situations with multiple KrakenSDR radios.

 <img width="333" alt="Screenshot 2024-02-15 at 8 59 32 PM" src="https://github.com/canaryradio/Kraken-to-TAK-Python/assets/127666889/1e5baa8e-ec39-444d-aaa5-5aae9b0979f2">

TAK Server
	-TAK Server IP should be the IPv4 of your TAK Server. TAK Server Port should be the port you assigned for the Kraken input. TAK Multicast will enable and disable sending Kraken packets to the multicast default for TAK clients

<img width="328" alt="Screenshot 2024-02-11 at 5 09 54 PM" src="https://github.com/canaryradio/Kraken-to-TAK-Python/assets/127666889/36be1a29-7fe7-4d11-b306-81c6f7ee7b9b">

Bearing Filter
	-DOA Ignore/Exclusion Range is the option to not build a packet for bearings that are coming from the specified direction. The reset button will remove the filter.

<img width="332" alt="Screenshot 2024-02-11 at 5 10 51 PM" src="https://github.com/canaryradio/Kraken-to-TAK-Python/assets/127666889/69abba4a-aaa0-4972-963e-5204c208bc8c">

New line with new UID
	-Persist DOA Line will randomize the UID of the Line XML payload which will create a new line for each DOA bearing instead of overwriting the same line.

<img width="330" alt="Screenshot 2024-02-11 at 5 11 29 PM" src="https://github.com/canaryradio/Kraken-to-TAK-Python/assets/127666889/1a7231f5-586b-49fe-89b5-749bdfb1fd35">

## GPS coordinate presence

The GPSD response adapter preserves absent latitude/longitude before gpsd-py3
can default them to zero. A partial fix uses the Kraken fallback; explicit zero
coordinates remain valid. The adapter replaces the dependency's response class
inside this process, without modifying installed packages or transport behavior.
Offline tests exercise both decoded packets and the real poll decoder with a
synthetic stream. The existing blocking GPSD read limitation still applies.

## Settings exposure: static review and remediation options

The current application starts Flask on all interfaces (`0.0.0.0:8000`), and
`/update_settings` has no authentication check. Any client able to reach it can
change persisted bridge settings and destinations. This is a source-code finding;
no running instance or network exposure was tested.

Before a separately authorized deployment, choose an access policy. Options are:

- Bind the dashboard to loopback for local administration, with authenticated
  tunneling if remote access is required.
- Restrict reachability to an approved management network using firewall rules.
- Put authentication and TLS at a reverse proxy, restricting direct access to
  the Flask listener so clients cannot bypass the proxy.
- Add application authentication and authorization for settings changes, with
  CSRF protection if browser sessions/cookies are used.

Network restriction reduces reachability but does not authenticate users on the
allowed network. Proxy/application controls need deployment-specific design and
validation. This repair documents these options only: bind addresses, routes,
credentials, live configuration, and security settings are unchanged.
