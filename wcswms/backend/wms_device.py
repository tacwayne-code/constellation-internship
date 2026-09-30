"""Persist PLC connection settings without issuing or replaying PLC commands."""
import ipaddress
import json
import logging
import os
import tempfile
from pathlib import Path

from .wms_service import WmsReader, feedback
from .wms_store import WmsError, now


def plc_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value.strip())
    except (ValueError, AttributeError):
        raise WmsError('请输入有效的 IPv4 地址，例如 192.168.0.100') from None
    if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_reserved:
        raise WmsError('请填写设备的单播 IPv4 地址，不能使用回环、广播或保留地址')
    return str(address)


def save_config(path, config):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            json.dump(config, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise WmsError('设备 IP 保存失败，原地址保持不变；请检查后台配置文件的写入权限和磁盘空间') from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class DeviceSettings:
    def __init__(self, config, path, service):
        self.config = config
        self.path = Path(path).resolve() if path is not None else None
        self.service = service
        self._publish()

    def _publish(self):
        self._settings = dict(host=self.config['host'], port=self.config.get('port', 102),
                              rack=self.config.get('rack', 0), slot=self.config.get('slot', 1),
                              revision=self.config.get('device_revision', 0),
                              updated_at=self.config.get('device_updated_at'),
                              editable=self.path is not None and isinstance(self.service.reader, WmsReader))

    def snapshot(self):
        # Do not wait for a PLC network timeout when rendering the web page.
        return self._settings.copy()

    def change_host(self, host, expected_revision):
        host = plc_ipv4(host)
        service, reader = self.service, self.service.reader
        with service.lock, reader.lock, service.store.lock:
            if not self._settings['editable']:
                raise WmsError('当前后台未配置可保存的设备设置，请使用正式 WMS 启动入口')
            try:
                disk = json.loads(self.path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError) as error:
                raise WmsError('无法读取后台设备配置，未切换地址') from error
            if disk != self.config:
                raise WmsError('后台配置文件已被修改，请重启后台后重新设置，避免覆盖其他配置')
            if host == self.config['host']:
                return dict(changed=False, device_settings=self.snapshot())
            if expected_revision != self._settings['revision']:
                raise WmsError('设备 IP 已被其他终端修改，请关闭设置后重新打开')
            ledger = service.store.read()
            if any(task['status'] not in {'QUEUED', 'COMPLETED', 'CANCELLED'} for task in ledger['tasks']):
                raise WmsError('有执行中、待核对或待返库任务，请完成后再修改设备 IP')
            if any(item['status'] in {'AT_STATION', 'AT_EXIT'} for item in ledger['stock'].values()):
                raise WmsError('出入口仍有料箱，请完成交接或返库后再修改设备 IP')
            # Fresh reads only. No acknowledge, stop, dispatch or automatic replay.
            if reader.snapshot().get('connected'):
                reader.poll()
            sample = reader.snapshot()
            status = feedback(sample).get('DB2.DBW0')
            if status not in (None, 8) or sample.get('command_code', 0) or sample.get('idle_ack', 0):
                raise WmsError('原 PLC 尚有任务或回执未复位，请核对设备空闲后再修改 IP')
            next_config = {**self.config, 'host': host, 'device_revision': self._settings['revision'] + 1,
                           'device_updated_at': now()}
            # Construct the empty sample before committing the config file.
            replacement = WmsReader(next_config)
            save_config(self.path, next_config)
            previous = self.config['host']
            self.config.update(next_config)
            reader.replace_connection(replacement, self.config)
            self._publish()
            logging.getLogger(__name__).info('WMS PLC IP changed from %s to %s', previous, host)
            return dict(changed=True, device_settings=self.snapshot())
