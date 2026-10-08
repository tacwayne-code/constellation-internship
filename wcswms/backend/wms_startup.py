"""Validate WMS startup without opening a ledger or connecting to a PLC."""
import ipaddress
import socket


def endpoint(config):
    if not isinstance(config, dict):
        raise ValueError('config/wms.json must contain a JSON object.')
    if config.get('profile') != 'PHYSICAL_CONTROL' or config.get('control_enabled') is not True:
        raise ValueError('WMS startup requires PHYSICAL_CONTROL / control_enabled=true in config/wms.json.')
    try:
        address = ipaddress.IPv4Address(config['listen_host'])
    except (KeyError, ValueError, TypeError):
        raise ValueError('config/wms.json listen_host must be this computer IPv4 address.') from None
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        raise ValueError('listen_host must be a host IPv4 address, not a wildcard, multicast or reserved address.')
    port = config.get('web_port')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('config/wms.json web_port must be an integer in 1024..65535.')
    return str(address), port


def check_local_address(host):
    # Port zero distinguishes an old/non-local IP from an occupied web port.
    # This never connects to the configured PLC or any remote host.
    try:
        with socket.socket() as probe:
            probe.bind((host, 0))
    except OSError as error:
        raise ValueError(
            f'WMS listen_host={host} cannot bind on this computer. '
            'Check the local IPv4 address and update config/wms.json listen_host '
            '(Get-NetIPAddress -AddressFamily IPv4). PLC host and inventory were not changed.'
        ) from error


def preflight(root, config):
    host, port = endpoint(config)
    if not (root / 'web/dist/wms.html').is_file():
        raise ValueError('WMS frontend is missing. Build web with pnpm build first.')
    check_local_address(host)
    try:
        with socket.socket() as probe:
            probe.bind((host, port))
    except OSError as error:
        raise ValueError(f'WMS web port is unavailable: {host}:{port}. No process was stopped.') from error
    return host, port
