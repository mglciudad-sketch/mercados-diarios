#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reporte diario de cierre de mercados: IBEX 35, Euro Stoxx 50, S&P 500 y Nasdaq 100.

Todas las cifras salen directamente de los datos (Yahoo Finance, API de Nasdaq,
Wikipedia para la composición de los índices). Nada se redacta con IA, así que
ningún número puede inventarse: si un dato falta, el reporte lo avisa.

Uso:
    python scripts/reporte_mercados.py              # genera, archiva y envía por email
    python scripts/reporte_mercados.py --sin-email  # genera y archiva, sin enviar
"""

import argparse
import html
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

RAIZ = Path(__file__).resolve().parent.parent
CACHE_COMPOSICION = RAIZ / "scripts" / "constituyentes.json"
CARPETA_ARCHIVO = RAIZ / "archivo" / "mercados"

MADRID = ZoneInfo("Europe/Madrid")
NUEVA_YORK = ZoneInfo("America/New_York")
CIERRE_EUROPA = dtime(17, 40)
CIERRE_EEUU = dtime(16, 5)

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

AVISO_FINAL = "Esto es un resumen informativo, no una recomendación de inversión."

URL_YAHOO = "https://finance.yahoo.com"
URL_NDX = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"

INDICES = [
    {"clave": "ibex", "nombre": "IBEX 35", "yahoo": "^IBEX", "zona": "eu",
     "wiki": "https://en.wikipedia.org/wiki/IBEX_35",
     "columnas": ("Ticker", "Company"), "rango": (33, 37)},
    {"clave": "sx5e", "nombre": "Euro Stoxx 50", "yahoo": "^STOXX50E", "zona": "eu",
     "wiki": "https://en.wikipedia.org/wiki/EURO_STOXX_50",
     "columnas": ("Ticker", "Name"), "rango": (48, 52)},
    {"clave": "spx", "nombre": "S&P 500", "yahoo": "^GSPC", "zona": "us",
     "wiki": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
     "columnas": ("Symbol", "Security"), "rango": (490, 510)},
    {"clave": "ndx", "nombre": "Nasdaq 100", "yahoo": "^NDX", "zona": "us"},
    {"clave": "msci", "nombre": "MSCI World", "yahoo": "^990100-USD-STRD", "zona": "us", "sin_ranking": True},
]

NOTA_MSCI = ("MSCI no publica gratis la composición diaria de sus más de 1.300 valores, así que este "
             "bloque no incluye los que más suben y más bajan.")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

fuentes_usadas: list[str] = []


def usar_fuente(url):
    if url not in fuentes_usadas:
        fuentes_usadas.append(url)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def reintentar(fn, intentos=3, espera=4):
    ultimo = None
    for i in range(intentos):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            log(f"  intento {i + 1}/{intentos} fallido: {e}")
            if i < intentos - 1:
                time.sleep(espera * (i + 1))
    raise ultimo


def get(url, timeout=40):
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- formato

def num(x, dec=2):
    s = f"{x:,.{dec}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def pct(x):
    return ("+" if x > 0 else "") + num(x) + "%"


def flecha(x):
    return "▲" if x > 0 else ("▼" if x < 0 else "=")


def fecha_corta(d: date):
    return f"{DIAS[d.weekday()][:3]} {d:%d/%m}"


def fecha_larga(d: date):
    return f"{DIAS[d.weekday()].capitalize()}, {d.day} de {MESES[d.month - 1]} de {d.year}"


def a_float_pct(texto):
    t = str(texto).strip().replace("%", "").replace(",", "")
    if t.upper() in ("UNCH", "0", "0.00"):
        return 0.0
    try:
        return float(t)
    except ValueError:
        return None


def normalizar_simbolo(s):
    return str(s).strip().upper().replace(".", "-").replace("/", "-")


def limpiar_nombre(nombre):
    n = re.sub(r"\s+(Class [A-C]\s+)?(Common Stock|Ordinary Shares|Common Shares|"
               r"American Depositary Shares|Depositary Shares).*$", "", str(nombre).strip())
    return n.strip(" ,")


# ---------------------------------------------------------------- datos

def composicion():
    """Composición de cada índice desde Wikipedia; si falla, la última guardada."""
    cache = {}
    if CACHE_COMPOSICION.exists():
        cache = json.loads(CACHE_COMPOSICION.read_text(encoding="utf-8"))
    resultado = {}
    for idx in INDICES:
        if "wiki" not in idx:
            continue
        col_t, col_n = idx["columnas"]
        lo, hi = idx["rango"]

        def leer(idx=idx, col_t=col_t, col_n=col_n, lo=lo, hi=hi):
            tablas = pd.read_html(io.StringIO(get(idx["wiki"]).text))
            for t in tablas:
                if col_t in t.columns and col_n in t.columns and lo <= len(t) <= hi:
                    return {str(r[col_t]).strip(): str(r[col_n]).strip() for _, r in t.iterrows()}
            raise ValueError("no se encontró la tabla de componentes")

        try:
            resultado[idx["clave"]] = reintentar(leer)
            usar_fuente(idx["wiki"])
            log(f"Composición {idx['nombre']}: {len(resultado[idx['clave']])} valores (Wikipedia)")
        except Exception as e:  # noqa: BLE001
            if idx["clave"] in cache:
                resultado[idx["clave"]] = cache[idx["clave"]]
                usar_fuente(idx["wiki"])
                log(f"Composición {idx['nombre']}: uso la copia guardada ({e})")
            else:
                log(f"Composición {idx['nombre']}: NO disponible ({e})")
    if "ndx" in cache:
        resultado["ndx"] = cache["ndx"]
    guardar_composicion(resultado, {})
    return resultado


def guardar_composicion(comp, nuevos):
    comp.update(nuevos)
    if comp:
        CACHE_COMPOSICION.write_text(json.dumps(comp, ensure_ascii=False, indent=1, sort_keys=True),
                                     encoding="utf-8")


def descargar_cierres(tickers, **periodo):
    periodo = periodo or {"period": "15d"}

    def bajar(lista):
        df = yf.download(lista, interval="1d", auto_adjust=False, progress=False,
                         threads=True, group_by="column", **periodo)
        if df is None or df.empty:
            raise ValueError("Yahoo Finance no devolvió datos")
        cierres = df["Close"]
        if isinstance(cierres, pd.Series):
            cierres = cierres.to_frame(lista[0])
        return cierres

    cierres = reintentar(lambda: bajar(tickers))
    vacios = [t for t in tickers if t not in cierres.columns or cierres[t].dropna().empty]
    if vacios and len(vacios) <= 25:
        log(f"Reintento individual en Yahoo para: {', '.join(vacios)}")
        for t in vacios:
            try:
                time.sleep(1)
                cierres[t] = reintentar(lambda t=t: bajar([t]))[t]
            except Exception as e:  # noqa: BLE001
                log(f"  {t}: sin datos ({e})")
    return cierres


def quitar_intradia(serie, zona):
    """Descarta la vela de HOY si el mercado aún no ha cerrado (sería un dato intradía)."""
    s = serie.dropna()
    s = s[[d.weekday() < 5 for d in s.index]]
    if s.empty:
        return s, False
    tz = MADRID if zona == "eu" else NUEVA_YORK
    ahora = datetime.now(tz)
    cierre = CIERRE_EUROPA if zona == "eu" else CIERRE_EEUU
    if s.index[-1].date() == ahora.date() and ahora.time() < cierre:
        return s.iloc[:-1], True
    return s, False


def variacion_hasta(serie, fecha_ref):
    s = serie.dropna()
    s = s[[d.date() <= fecha_ref for d in s.index]]
    if len(s) < 2 or s.index[-1].date() != fecha_ref:
        return None
    return float(s.iloc[-1] / s.iloc[-2] - 1) * 100


def datos_indices():
    cierres = descargar_cierres([i["yahoo"] for i in INDICES])
    usar_fuente(URL_YAHOO)
    out = {}
    for idx in INDICES:
        clave = idx["clave"]
        if idx["yahoo"] not in cierres.columns:
            out[clave] = {"error": "cierre"}
            continue
        s, intradia = quitar_intradia(cierres[idx["yahoo"]], idx["zona"])
        if len(s) < 2:
            out[clave] = {"error": "cierre"}
            continue
        out[clave] = {
            "fecha": s.index[-1].date(),
            "nivel": float(s.iloc[-1]),
            "var": float(s.iloc[-1] / s.iloc[-2] - 1) * 100,
            "intradia_descartado": intradia,
        }
    return out


def ranking_yahoo(nombres_por_ticker, fecha_ref, zona):
    cierres = descargar_cierres(list(nombres_por_ticker))
    usar_fuente(URL_YAHOO)
    filas = []
    for ticker, nombre in nombres_por_ticker.items():
        if ticker not in cierres.columns:
            continue
        s, _ = quitar_intradia(cierres[ticker], zona)
        v = variacion_hasta(s, fecha_ref)
        if v is not None:
            filas.append((nombre, v))
    return filas


def ranking_nasdaq100(fecha_ref, nombres_sp, comp):
    """API oficial de Nasdaq; si falla o trae otra fecha, Yahoo con la última composición guardada."""
    try:
        j = reintentar(lambda: get(URL_NDX).json())
        fecha = datetime.strptime(j["data"]["date"], "%b %d, %Y").date()
        filas_api = j["data"]["data"]["rows"]
        nombres = {normalizar_simbolo(r["symbol"]): nombres_sp.get(normalizar_simbolo(r["symbol"]))
                   or limpiar_nombre(r["companyName"]) for r in filas_api}
        guardar_composicion(comp, {"ndx": nombres})
        if fecha != fecha_ref:
            raise ValueError(f"la API de Nasdaq da datos del {fecha}, el índice cerró el {fecha_ref}")
        filas = []
        for r in filas_api:
            v = a_float_pct(r.get("percentageChange"))
            if v is not None:
                filas.append((nombres[normalizar_simbolo(r["symbol"])], v))
        usar_fuente(URL_NDX)
        return filas, len(filas_api)
    except Exception as e:  # noqa: BLE001
        log(f"Nasdaq 100 vía API de Nasdaq: {e}; uso Yahoo Finance")
        if "ndx" not in comp:
            raise
        return ranking_yahoo(comp["ndx"], fecha_ref, "us"), len(comp["ndx"])


# ---------------------------------------------------------------- análisis

def tono(v):
    if v >= 1.5:
        return "Jornada de fuertes subidas"
    if v >= 0.5:
        return "Sesión positiva"
    if v > 0.15:
        return "Sesión de ligeras subidas"
    if v >= -0.15:
        return "Sesión sin grandes cambios"
    if v > -0.5:
        return "Sesión de ligeras caídas"
    if v > -1.5:
        return "Sesión negativa"
    return "Jornada de fuertes caídas"


def frase_contexto(var_indice, filas):
    n = len(filas)
    suben = sum(1 for _, v in filas if v > 0)
    v = var_indice if var_indice is not None else 0.0
    base = tono(v)
    if v >= 0:
        lider, lv = max(filas, key=lambda x: x[1])
        final = f"{lider} ({pct(lv)}) fue el que más subió"
    else:
        lider, lv = min(filas, key=lambda x: x[1])
        final = f"{lider} ({pct(lv)}) fue el más castigado"
    return f"{base}: {suben} de {n} valores cerraron al alza y {final}."


def construir_bloques():
    comp = composicion()
    try:
        niveles = datos_indices()
    except Exception as e:  # noqa: BLE001
        log(f"Índices: fallo en Yahoo Finance ({e})")
        niveles = {i["clave"]: {"error": "cierre"} for i in INDICES}

    nombres_sp = {normalizar_simbolo(k): v for k, v in comp.get("spx", {}).items()}
    bloques = {}
    for idx in INDICES:
        clave = idx["clave"]
        b = {"nombre": idx["nombre"], "avisos": [], **niveles[clave]}
        if b.get("error"):
            b["avisos"].append(f"No se ha podido obtener de forma fiable el cierre del {idx['nombre']} "
                               "(nivel y variación %).")
        if b.get("intradia_descartado"):
            b["avisos"].append(f"{idx['nombre']}: había un dato intradía de hoy; se ha descartado "
                               "y se muestra el último cierre.")
        bloques[clave] = b

    for idx in INDICES:
        clave, b = idx["clave"], bloques[idx["clave"]]
        if idx.get("sin_ranking"):
            b["suben"], b["bajan"] = [], []
            b["frase"] = (f"{tono(b['var'])} para la bolsa mundial en su conjunto."
                          if b.get("var") is not None else None)
            b["avisos"].append(NOTA_MSCI)
            continue
        filas, total = [], None
        try:
            if b.get("fecha") is None:
                raise ValueError("sin fecha de cierre de referencia")
            if clave == "ndx":
                filas, total = ranking_nasdaq100(b["fecha"], nombres_sp, comp)
            else:
                nombres = nombres_sp if clave == "spx" else comp.get(clave)
                if not nombres:
                    raise ValueError("sin composición del índice")
                total = len(nombres)
                filas = ranking_yahoo(nombres, b["fecha"], idx["zona"])
        except Exception as e:  # noqa: BLE001
            log(f"Ranking {idx['nombre']}: {e}")

        if len(filas) < 6:
            b["avisos"].append(f"No se han podido obtener de forma fiable los valores que más suben "
                               f"y más bajan del {idx['nombre']}.")
            b["suben"], b["bajan"], b["frase"] = [], [], None
            continue
        if total and len(filas) < 0.9 * total:
            b["avisos"].append(f"{idx['nombre']}: ranking calculado con {len(filas)} de {total} valores "
                               "(faltan datos de algunos).")
        # Solo cuenta como "sube" o "baja" lo que realmente subió o bajó: un día casi todo al alza
        # puede tener menos de 3 valores en negativo, y no se rellena con los "menos buenos".
        b["suben"] = [f for f in sorted(filas, key=lambda x: x[1], reverse=True) if f[1] > 0][:3]
        b["bajan"] = [f for f in sorted(filas, key=lambda x: x[1]) if f[1] < 0][:3]
        b["frase"] = frase_contexto(b.get("var"), filas)
        b["cobertura"] = (len(filas), total)
        b["al_alza"] = sum(1 for _, v in filas if v > 0)
    return [bloques[i["clave"]] for i in INDICES]


# ---------------------------------------------------------------- salida

def texto_reporte(hoy, bloques):
    lineas = [f"REPORTE DIARIO DE MERCADOS — {hoy:%d/%m/%Y}", "Cierres de la sesión anterior", ""]
    for b in bloques:
        lineas.append(b["nombre"].upper())
        if not b.get("error"):
            lineas.append(f"Cierre ({fecha_corta(b['fecha'])}): {num(b['nivel'])} puntos ({pct(b['var'])})")
        if b.get("cobertura"):
            lineas.append("Más suben: " + (" · ".join(f"{n} {pct(v)}" for n, v in b["suben"])
                                           or "ningún valor cerró al alza"))
            lineas.append("Más bajan: " + (" · ".join(f"{n} {pct(v)}" for n, v in b["bajan"])
                                           or "ningún valor cerró a la baja"))
        if b["frase"]:
            lineas.append(b["frase"])
        for a in b["avisos"]:
            lineas.append(f"⚠ Aviso: {a}")
        lineas.append("")
    if os.environ.get("PANEL_URL"):
        lineas.append(f"Panel completo: {os.environ['PANEL_URL']}")
        lineas.append("")
    lineas.append(AVISO_FINAL)
    lineas.append("Fuentes: " + ", ".join(fuentes_usadas))
    return "\n".join(lineas)


def html_reporte(hoy, bloques):
    C = {"primary": "#0F1F3D", "medium": "#2E4A7D", "pale": "#D6E0F0", "accent": "#D4AF37",
         "soft": "#F8EFD4", "muted": "#6B6B6B", "alt": "#F2F2F2", "dmuted": "#A0A8B8"}
    sans = "'IBM Plex Sans',Arial,Helvetica,sans-serif"
    mono = "'IBM Plex Mono',Consolas,'Courier New',monospace"
    e = html.escape

    resumen = "".join(
        f'<td width="20%" style="padding:10px 2px;text-align:center;border-right:1px solid {C["medium"]};">'
        f'<div style="font-size:10px;color:{C["dmuted"]};">{e(b["nombre"])}</div>'
        f'<div style="font-family:{mono};font-size:12px;font-weight:600;color:#FFFFFF;margin-top:2px;'
        f'white-space:nowrap;">'
        + (f'{flecha(b["var"])} {pct(b["var"])}' if not b.get("error") else "s/d")
        + "</div></td>" for b in bloques)

    def filas_tabla(titulo, filas, vacio):
        h = (f'<tr><td colspan="2" style="background:{C["primary"]};color:#FFFFFF;padding:6px 10px;'
             f'font-weight:600;font-size:13px;">{titulo}</td></tr>')
        if not filas:
            h += (f'<tr><td colspan="2" style="padding:6px 10px;color:{C["muted"]};font-size:13px;'
                  f'font-style:italic;">{vacio}</td></tr>')
        for i, (n, v) in enumerate(filas):
            bg = C["alt"] if i % 2 else "#FFFFFF"
            h += (f'<tr style="background:{bg};"><td style="padding:6px 10px;color:{C["primary"]};'
                  f'font-size:14px;">{e(n)}</td><td align="right" style="padding:6px 10px;font-family:{mono};'
                  f'font-size:14px;color:{C["primary"]};white-space:nowrap;">{flecha(v)} {pct(v)}</td></tr>')
        return h

    cuerpo = ""
    for b in bloques:
        cuerpo += (f'<tr><td style="padding:22px 22px 0 22px;">'
                   f'<div style="font-size:19px;font-weight:600;color:{C["primary"]};padding-bottom:6px;'
                   f'border-bottom:2px solid {C["accent"]};">{e(b["nombre"])}</div>')
        if not b.get("error"):
            cuerpo += (f'<div style="margin-top:10px;font-size:14px;color:{C["primary"]};">'
                       f'Cierre del {fecha_corta(b["fecha"])}: '
                       f'<span style="font-family:{mono};font-weight:600;">{num(b["nivel"])}</span> puntos '
                       f'<span style="font-family:{mono};font-weight:600;white-space:nowrap;">'
                       f'{flecha(b["var"])} {pct(b["var"])}</span></div>')
        if b.get("cobertura"):
            cuerpo += (f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;'
                       f'border-collapse:collapse;border:1px solid {C["primary"]};">'
                       + filas_tabla("▲ Más suben", b["suben"], "Ningún valor cerró al alza.")
                       + filas_tabla("▼ Más bajan", b["bajan"], "Ningún valor cerró a la baja.")
                       + "</table>")
        if b["frase"]:
            cuerpo += (f'<div style="margin-top:12px;background:{C["soft"]};border-left:3px solid {C["accent"]};'
                       f'padding:9px 11px;font-size:14px;color:{C["primary"]};">{e(b["frase"])}</div>')
        for a in b["avisos"]:
            cuerpo += (f'<div style="margin-top:10px;background:{C["pale"]};border-left:3px solid {C["primary"]};'
                       f'padding:9px 11px;font-size:13px;color:{C["primary"]};"><b>Aviso:</b> {e(a)}</div>')
        cuerpo += "</td></tr>"

    fuentes = " · ".join(f'<a href="{e(u)}" style="color:{C["medium"]};">{e(u)}</a>' for u in fuentes_usadas)
    boton_panel = ""
    if os.environ.get("PANEL_URL"):
        boton_panel = (f'<tr><td style="padding:22px 22px 0 22px;" align="center">'
                       f'<a href="{e(os.environ["PANEL_URL"])}" style="display:inline-block;background:{C["primary"]};'
                       f'color:{C["accent"]};font-weight:600;font-size:14px;text-decoration:none;padding:11px 20px;">'
                       f'Ver panel completo →</a></td></tr>')

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reporte diario de mercados {hoy:%d/%m/%Y}</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:{C['alt']};font-family:{sans};">
<table width="100%" cellpadding="0" cellspacing="0" style="background:{C['alt']};"><tr><td align="center" style="padding:12px 6px;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#FFFFFF;border:1px solid {C['pale']};">
<tr><td style="background:{C['primary']};padding:20px 22px 14px 22px;">
<div style="font-size:12px;letter-spacing:2px;font-weight:600;color:{C['accent']};">REPORTE DE MERCADOS</div>
<div style="font-size:22px;font-weight:600;color:#FFFFFF;margin-top:6px;">{fecha_larga(hoy)}</div>
<div style="font-size:12px;color:{C['dmuted']};margin-top:4px;">Cierres de la sesión anterior</div>
</td></tr>
<tr><td style="background:{C['primary']};padding:0 14px 14px 14px;">
<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid {C['medium']};"><tr>{resumen}</tr></table>
</td></tr>
{cuerpo}
{boton_panel}
<tr><td style="padding:24px 22px 0 22px;">
<div style="background:{C['primary']};color:#FFFFFF;font-style:italic;padding:9px 12px;font-size:13px;">{e(AVISO_FINAL)}</div>
<div style="margin-top:10px;font-size:11px;line-height:1.5;color:{C['muted']};">Fuentes: {fuentes}</div>
</td></tr>
<tr><td style="padding:16px 22px 20px 22px;border-top:0;">
<div style="border-top:1px solid {C['primary']};padding-top:8px;font-size:11px;color:{C['muted']};">Elaborado por Miguel Ciudad · generado automáticamente de lunes a sábado a partir de las 7:00</div>
</td></tr>
</table></td></tr></table>
</body></html>"""


def guardar_historico(hoy, bloques):
    ruta = CARPETA_ARCHIVO / "historico.json"
    historico = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    registro = {}
    for idx, b in zip(INDICES, bloques):
        registro[idx["clave"]] = {
            "fecha_cierre": b["fecha"].isoformat() if b.get("fecha") else None,
            "nivel": round(b["nivel"], 2) if b.get("nivel") is not None else None,
            "var_pct": round(b["var"], 3) if b.get("var") is not None else None,
            "suben": [[n, round(v, 2)] for n, v in b["suben"]],
            "bajan": [[n, round(v, 2)] for n, v in b["bajan"]],
            "al_alza": b.get("al_alza"),
            "valores": b["cobertura"][0] if b.get("cobertura") else None,
            "frase": b.get("frase"),
            "avisos": b["avisos"],
        }
    historico[hoy.isoformat()] = registro
    ruta.write_text(json.dumps(historico, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def enviar_email(asunto, cuerpo_html, cuerpo_texto):
    clave = os.environ["RESEND_API_KEY"]
    payload = {
        "from": os.environ.get("EMAIL_REMITENTE", "Reporte Mercados <onboarding@resend.dev>"),
        "to": [os.environ["EMAIL_DESTINATARIO"]],
        "subject": asunto,
        "html": cuerpo_html,
        "text": cuerpo_texto,
    }

    def mandar():
        r = requests.post("https://api.resend.com/emails", json=payload, timeout=30,
                          headers={"Authorization": f"Bearer {clave}"})
        if r.status_code >= 300:
            raise RuntimeError(f"Resend respondió {r.status_code}: {r.text}")
        return r.json().get("id")

    log(f"Email enviado (id {reintentar(mandar)})")


def main():
    ap = argparse.ArgumentParser(description="Reporte diario de mercados")
    ap.add_argument("--sin-email", action="store_true", help="no enviar el email")
    ap.add_argument("--html", help="guardar también el HTML en esta ruta")
    ap.add_argument("--exigir-completo", action="store_true",
                    help="si falta algún dato, no enviar nada y salir con código 3 (se reintentará)")
    args = ap.parse_args()

    hoy = datetime.now(MADRID).date()
    bloques = construir_bloques()
    texto = texto_reporte(hoy, bloques)
    cuerpo_html = html_reporte(hoy, bloques)

    palabras = len(texto.split("Fuentes:")[0].split())
    log(f"Reporte generado: {palabras} palabras (sin contar fuentes)")

    sys.stdout.reconfigure(encoding="utf-8")
    print(texto)

    faltan = [a for b in bloques for a in b["avisos"] if a != NOTA_MSCI and "dato intradía" not in a]
    if args.exigir_completo and faltan:
        log("Datos incompletos; no se envía para reintentarlo más tarde: " + " | ".join(faltan))
        sys.exit(3)

    if not args.sin_email and not os.environ.get("RESEND_API_KEY"):
        print("::warning::Falta el secreto RESEND_API_KEY: hoy no se envía el email", flush=True)
    elif not args.sin_email:
        enviar_email(f"Reporte diario de mercados — {hoy:%d/%m/%Y}", cuerpo_html, texto)

    # Se archiva después del envío: que exista el reporte de hoy significa que el email ya salió.
    CARPETA_ARCHIVO.mkdir(parents=True, exist_ok=True)
    (CARPETA_ARCHIVO / f"{hoy:%Y-%m-%d}.md").write_text(texto + "\n", encoding="utf-8")
    guardar_historico(hoy, bloques)
    if args.html:
        Path(args.html).write_text(cuerpo_html, encoding="utf-8")

    if all(b.get("error") for b in bloques):
        sys.exit("No se ha podido obtener ningún dato de mercado")


if __name__ == "__main__":
    main()
