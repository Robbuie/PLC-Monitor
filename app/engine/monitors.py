"""Building a PLC driver and a socket monitor from the saved configuration.

One function each, used by the window and by the headless runner alike. Before
1.0 the two built them separately and had drifted: the runner ignored the
libplctag settings, the chosen network adapter and Passive Sniff entirely, so a
setup that worked in the window did something different -- or nothing -- once
it was installed as a service. Now there is one way to read a config into a
monitor, and the window saves before it connects, so what it tested is what the
service runs.
"""

from __future__ import annotations

from typing import Callable

from app.engine.event_log import EventLogger
from app.engine.plc_drivers import DEFAULT_PORTS, DEFAULT_SLOTS, PLCDriver, create_driver

LIBPLCTAG = "Rockwell (libplctag)"
SIEMENS = "Siemens (snap7)"


class ConfigError(ValueError):
    """A setting that cannot be turned into a monitor, worded for a person."""


def _int(value, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a whole number (got {value!r}).") from None


def build_driver(plc: dict) -> PLCDriver:
    """A driver for the `plc` section, not yet connected."""
    kind = plc.get("type", "Rockwell (pycomm3)")
    ip = str(plc.get("ip", "")).strip()
    if not ip:
        raise ConfigError("Enter the PLC's IP address.")
    source_ip = str(plc.get("source_ip", "") or "")

    if kind == LIBPLCTAG:
        from app.engine.libplctag_driver import LibPlcTagConfig
        lpt = plc.get("libplctag", {}) or {}
        cfg = LibPlcTagConfig(
            gateway=ip,
            path=str(lpt.get("path", "1,0")).strip(),
            cpu=str(lpt.get("cpu", "lgx")).strip() or "lgx",
            protocol=str(lpt.get("protocol", "ab-eip")).strip() or "ab-eip",
            data_type=str(lpt.get("data_type", "DINT")).strip() or "DINT",
            timeout_ms=_int(lpt.get("timeout_ms", 5000), "Timeout"),
            debug=_int(lpt.get("debug", 0), "Debug level"),
            dll_path=str(lpt.get("dll_path", "") or "").strip(),
        )
        types = {}
        for item in (plc.get("tags_per_driver") or {}).get(kind, []) or []:
            if isinstance(item, dict) and item.get("tag") and item.get("dtype"):
                types[str(item["tag"])] = str(item["dtype"])
        return create_driver(kind, ip, cfg=cfg, source_ip=source_ip, tag_types=types)

    port = _int(plc.get("port", DEFAULT_PORTS.get(kind, 44818)), "Port")
    slot = _int(plc.get("slot", DEFAULT_SLOTS.get(kind, 0)), "Slot")
    if kind == SIEMENS:
        rack = _int(plc.get("rack", 0), "Rack")
        return create_driver(kind, ip, slot=slot, rack=rack, port=port, source_ip=source_ip)
    return create_driver(
        kind, ip, slot=slot, port=port, source_ip=source_ip,
        plc_sub_type=plc.get("plc_sub_type", "Q"), frame=plc.get("frame", "3E"),
    )


def specs_for(plc: dict) -> list:
    """The tags for the selected driver, as TagSpecs (address, grouping,
    format, name).

    `tags_per_driver` is the truth since per-driver lists arrived; `tags` is the
    older flat list, still read for a config that predates it.
    """
    from app.engine.tagspec import specs_from
    kind = plc.get("type", "")
    per = (plc.get("tags_per_driver") or {}).get(kind)
    if per:
        return specs_from(per)
    return specs_from([str(t) for t in plc.get("tags", []) if t])


def tags_for(plc: dict) -> list[str]:
    """The names the selected driver's tags are logged under."""
    return [spec.key for spec in specs_for(plc)]


def build_socket_monitor(sock: dict, logger: EventLogger,
                         status_cb: Callable[[str], None] | None = None):
    """A socket monitor for the `socket` section, not yet started."""
    from app.engine.socket_proxy import SocketProxy, SocketServer, UDPMonitor

    mode = sock.get("mode", "passive")
    label = str(sock.get("source_label") or "SOCKET")

    if mode == "passive":
        from app.engine.passive_sniffer import PassiveSniffer, build_bpf_filter, scapy_available
        ok, err = scapy_available()
        if not ok:
            raise ConfigError(err)
        sniff = sock.get("sniff", {}) or {}
        bpf = str(sniff.get("bpf", "") or "").strip() or build_bpf_filter(
            str(sniff.get("host", "")).strip(), str(sniff.get("port", "")).strip(),
            str(sniff.get("proto", "any")))
        if not bpf:
            raise ConfigError("Enter a filter host or port, so the capture knows "
                              "what to watch. An empty filter captures everything "
                              "on the adapter.")
        return PassiveSniffer(
            logger=logger, bpf_filter=bpf, iface=(sniff.get("iface") or None),
            source_label=label, our_ip=str(sniff.get("our_ip", "")).strip(),
            status_cb=status_cb,
        )

    lhost = str(sock.get("listen_host", "0.0.0.0")).strip() or "0.0.0.0"
    lport = _int(sock.get("listen_port", 9000), "Listen port")
    if mode == "proxy":
        fhost = str(sock.get("forward_host", "")).strip()
        if not fhost:
            raise ConfigError("Enter the forward host: the receiver the proxy passes traffic on to.")
        fport = _int(sock.get("forward_port", 9001), "Forward port")
        return SocketProxy(logger=logger, listen_host=lhost, listen_port=lport,
                           forward_host=fhost, forward_port=fport,
                           source_label=label, status_cb=status_cb)
    if mode == "server":
        return SocketServer(logger=logger, listen_host=lhost, listen_port=lport,
                            source_label=label, status_cb=status_cb)
    if mode == "udp":
        return UDPMonitor(logger=logger, listen_host=lhost, listen_port=lport,
                          source_label=label, status_cb=status_cb)
    raise ConfigError(f"Unknown socket mode {mode!r}.")


def describe_socket(sock: dict) -> str:
    """One line for a status chip: what the socket monitor is doing."""
    mode = sock.get("mode", "passive")
    if mode == "passive":
        sniff = sock.get("sniff", {}) or {}
        target = " ".join(x for x in (str(sniff.get("host", "")), str(sniff.get("port", ""))) if x)
        return f"sniffing {target}".strip()
    if mode == "proxy":
        return f"proxy :{sock.get('listen_port')} to {sock.get('forward_host')}:{sock.get('forward_port')}"
    return f"{mode} :{sock.get('listen_port')}"
