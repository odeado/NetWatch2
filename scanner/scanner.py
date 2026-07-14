#!/usr/bin/env python3
"""
Win NetWatch RMM - Modulo de Escaneo de Red
=============================================
Descubre hosts activos dentro de subredes, detecta puertos clave de Windows
(RDP/SMB/RPC/NetBIOS/WinRM), resuelve hostname via DNS inverso, captura MAC
via tabla ARP local y calcula un score de confianza para evitar falsos
positivos (IPs flotantes).

Un host se considera activo si responde al ping O si tiene algun puerto
clave abierto (muchos equipos corporativos bloquean ICMP por firewall pero
si exponen RDP/SMB/WinRM, asi que el escaneo de puertos se hace siempre,
sin depender de que el ping haya respondido).

Solo usa la libreria estandar de Python (sin dependencias externas), para
poder correr de inmediato en cualquier PC con Python 3 instalado.

Uso:
    python scanner.py                          Escanea la subred por defecto (config.json: default_subnet)
    python scanner.py --local                  Autodetecta y escanea tu subred actual
    python scanner.py --subnet 192.168.1.0/24  Escanea una subred especifica
    python scanner.py --all                    Escanea todas las subredes de config.json
    python scanner.py --local --workers 128    Ajusta la concurrencia
"""

import argparse
import ipaddress
import json
import platform
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

IS_WINDOWS = platform.system().lower() == "windows"
BASE_DIR = Path(__file__).resolve().parent

# En Windows, cada ping/arp lanza un subproceso nuevo. Si el proceso padre no
# tiene consola propia (por ejemplo corriendo con pythonw, o como tarea
# programada), Windows puede intentar abrirle una consola/ventana a cada uno
# de esos subprocesos -- con hasta ~64 en paralelo por ciclo, eso se traduce
# en un aluvion de ventanas/dialogos de error que llego a colgar el equipo
# (visto en la practica el 2026-07-13). CREATE_NO_WINDOW le dice a Windows
# que corra el subproceso sin ventana ni consola, sin importar como se haya
# lanzado el proceso padre.
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

def load_config(path=None):
    cfg_path = Path(path) if path else BASE_DIR / "config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def detect_local_subnet():
    """Detecta la subred /24 de la interfaz de red principal del equipo."""
    local_ip = None

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no envia datos, solo resuelve la ruta
        local_ip = s.getsockname()[0]
        s.close()
    except OSError:
        pass

    if not local_ip:
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except socket.gaierror:
            pass

    if not local_ip or local_ip.startswith("127."):
        raise RuntimeError(
            "No se pudo autodetectar la subred local (sin red/DNS disponible en este equipo). "
            "Usa --subnet <CIDR> indicando manualmente tu red, ej: --subnet 192.168.1.0/24"
        )

    parts = local_ip.split(".")
    cidr = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    return cidr, local_ip


# --------------------------------------------------------------------------
# Ping
# --------------------------------------------------------------------------

def ping_host(ip, timeout_ms=800, retries=1):
    """Devuelve (alive, latency_ms, loss_pct) usando el ping nativo del SO."""
    count = retries + 1
    if IS_WINDOWS:
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), ip]
    else:
        timeout_sec = max(1, round(timeout_ms / 1000))
        cmd = ["ping", "-c", str(count), "-W", str(timeout_sec), ip]

    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=(timeout_ms / 1000) * count + 2, text=True,
            creationflags=_SUBPROCESS_FLAGS,
        )
        output = result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False, None, 100.0

    alive = result.returncode == 0

    loss_pct = 100.0
    loss_match = re.search(r"(\d+)%\s*(?:packet)?\s*loss", output, re.IGNORECASE)
    if loss_match:
        loss_pct = float(loss_match.group(1))

    latency_ms = None
    lat_match = re.search(r"(?:Average|avg)[^=]*=\s*(\d+(?:\.\d+)?)", output, re.IGNORECASE)
    if not lat_match:
        lat_match = re.search(r"time[=<]([\d.]+)\s*ms", output, re.IGNORECASE)
    if lat_match:
        latency_ms = float(lat_match.group(1))

    if not alive and loss_pct < 100.0:
        alive = True  # hubo al menos una respuesta aunque el codigo de salida difiera

    return alive, latency_ms, loss_pct


# --------------------------------------------------------------------------
# Puertos
# --------------------------------------------------------------------------

def scan_ports(ip, ports, timeout=0.6):
    open_ports = []
    for p in ports:
        port = p["port"]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                if sock.connect_ex((ip, port)) == 0:
                    open_ports.append({"port": port, "label": p["label"]})
        except OSError:
            continue
    return open_ports


# --------------------------------------------------------------------------
# DNS inverso
# --------------------------------------------------------------------------

def reverse_dns(ip, timeout=0.8):
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        hostname = socket.gethostbyaddr(ip)[0]
        return hostname
    except (socket.herror, socket.gaierror, socket.timeout):
        return None
    finally:
        socket.setdefaulttimeout(old_timeout)


# --------------------------------------------------------------------------
# MAC via tabla ARP
# --------------------------------------------------------------------------

_arp_cache = None


def _load_arp_table():
    global _arp_cache
    if _arp_cache is not None:
        return _arp_cache

    table = {}
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                ["arp", "-a"], stdout=subprocess.PIPE, text=True, timeout=5,
                creationflags=_SUBPROCESS_FLAGS,
            ).stdout
            for line in out.splitlines():
                m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})", line)
                if m:
                    ip_addr = m.group(1)
                    mac = m.group(2).replace("-", ":").upper()
                    table[ip_addr] = mac
        else:
            out = subprocess.run(["ip", "neigh"], stdout=subprocess.PIPE, text=True, timeout=5).stdout
            for line in out.splitlines():
                m = re.match(r"(\d+\.\d+\.\d+\.\d+)\s+.*lladdr\s+([0-9a-fA-F:]{17})", line)
                if m:
                    table[m.group(1)] = m.group(2).upper()
    except (OSError, subprocess.TimeoutExpired):
        pass

    _arp_cache = table
    return table


def get_mac(ip):
    return _load_arp_table().get(ip)


# --------------------------------------------------------------------------
# Score de confianza
# --------------------------------------------------------------------------

def confidence_score(ping_alive, loss_pct, open_ports, hostname, mac):
    """
    El puerto abierto (RDP/SMB/RPC/WinRM) es la senal mas fuerte de que hay un
    equipo real detras de la IP: muchos firewalls corporativos bloquean ICMP
    pero dejan pasar estos puertos, asi que pesa mas que el ping.
    """
    score = 0
    if ping_alive:
        score += max(0, 30 - int(loss_pct * 0.3))  # hasta 30 pts segun perdida de paquetes
    score += 50 if open_ports else 0
    score += 10 if hostname else 0
    score += 10 if mac else 0

    if score >= 45:
        label = "confiable"
    elif score >= 20:
        label = "posible_falso_positivo"
    else:
        label = "no_confiable"

    return score, label


# --------------------------------------------------------------------------
# Escaneo de un host / de una subred
# --------------------------------------------------------------------------

def scan_host(ip, config):
    ip = str(ip)
    ping_cfg = config.get("ping", {})
    ping_alive, latency_ms, loss_pct = ping_host(
        ip, ping_cfg.get("timeout_ms", 800), ping_cfg.get("retries", 1)
    )

    # El escaneo de puertos se hace siempre, incluso si el ping no respondio:
    # equipos con ICMP bloqueado por firewall igual pueden tener RDP/SMB abiertos.
    port_timeout = config.get("port_scan", {}).get("timeout_sec", 0.6)
    open_ports = scan_ports(ip, config.get("ports", []), port_timeout)

    alive = ping_alive or bool(open_ports)

    if not alive:
        return {
            "ip": ip, "alive": False, "latency_ms": None, "loss_pct": loss_pct,
            "open_ports": [], "hostname": None, "mac": None,
            "confidence_score": 0, "confidence_label": "sin_respuesta",
        }

    hostname = reverse_dns(ip)
    mac = get_mac(ip)
    score, label = confidence_score(ping_alive, loss_pct, open_ports, hostname, mac)

    return {
        "ip": ip, "alive": True,
        "latency_ms": latency_ms if ping_alive else None,
        "loss_pct": loss_pct if ping_alive else None,
        "open_ports": open_ports, "hostname": hostname, "mac": mac,
        "confidence_score": score, "confidence_label": label,
    }


def scan_subnet(cidr, config, max_workers=64, on_progress=None):
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = list(network.hosts())
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scan_host, ip, config): ip for ip in hosts}
        done = 0
        for future in as_completed(futures):
            results.append(future.result())
            done += 1
            if on_progress:
                on_progress(done, len(hosts))

    results.sort(key=lambda r: ipaddress.ip_address(r["ip"]))
    return results


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def print_progress(done, total):
    pct = int(done / total * 100)
    bar = "#" * (pct // 4)
    sys.stdout.write(f"\r  [{bar:<25}] {done}/{total} ({pct}%)")
    sys.stdout.flush()
    if done == total:
        sys.stdout.write("\n")


def print_table(results):
    alive = [r for r in results if r["alive"]]
    print(f"\n  Hosts activos: {len(alive)} / {len(results)} direcciones escaneadas\n")
    if not alive:
        return

    header = f"{'IP':<16}{'Hostname':<26}{'MAC':<19}{'Latencia':<10}{'Puertos':<24}{'Confianza'}"
    print(header)
    print("-" * len(header))
    for r in alive:
        ports_str = ",".join(str(p["port"]) for p in r["open_ports"]) or "-"
        latency = f"{r['latency_ms']:.0f}ms" if r["latency_ms"] else "-"
        print(
            f"{r['ip']:<16}{(r['hostname'] or '-')[:24]:<26}{(r['mac'] or '-'):<19}"
            f"{latency:<10}{ports_str:<24}{r['confidence_label']}"
        )


def save_json(all_results, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"scan_{ts}.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "subnets": list(all_results.keys()),
        "results": all_results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Win NetWatch RMM - Escaner de red")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--local", action="store_true", help="Autodetecta y escanea tu subred actual")
    group.add_argument("--subnet", metavar="CIDR", help="Escanea una subred especifica, ej: 192.168.1.0/24")
    group.add_argument("--all", action="store_true", help="Escanea todas las subredes de config.json")
    parser.add_argument("--config", default=None, help="Ruta a config.json alternativo")
    parser.add_argument("--workers", type=int, default=None, help="Hilos concurrentes (default: config.json)")
    parser.add_argument("--out", default=str(BASE_DIR / "results"), help="Carpeta de salida para el JSON")
    args = parser.parse_args()

    config = load_config(args.config)
    max_workers = args.workers or config.get("concurrency", {}).get("max_workers", 64)

    subnets_to_scan = []
    if args.local:
        try:
            cidr, local_ip = detect_local_subnet()
        except RuntimeError as e:
            print(f"Error: {e}")
            sys.exit(1)
        print(f"Subred local detectada: {cidr}  (tu IP: {local_ip})")
        subnets_to_scan.append({"cidr": cidr, "label": "local (autodetectada)"})
    elif args.subnet:
        subnets_to_scan.append({"cidr": args.subnet, "label": "manual"})
    elif args.all:
        subnets_to_scan = config.get("subnets", [])
    else:
        # Sin flags: usar la subred por defecto configurada para el trabajo
        default_cidr = config.get("default_subnet", "172.30.100.0/24")
        subnets_to_scan.append({"cidr": default_cidr, "label": "predeterminada (trabajo)"})

    all_results = {}
    start = time.time()
    for sn in subnets_to_scan:
        print(f"\nEscaneando {sn['cidr']} ({sn['label']})...")
        results = scan_subnet(sn["cidr"], config, max_workers, on_progress=print_progress)
        all_results[sn["cidr"]] = results
        print_table(results)

    elapsed = time.time() - start
    out_path = save_json(all_results, args.out)
    print(f"\nEscaneo completo en {elapsed:.1f}s. Resultados guardados en: {out_path}")


if __name__ == "__main__":
    main()
