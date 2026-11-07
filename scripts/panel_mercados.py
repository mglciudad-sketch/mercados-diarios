#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera el panel web "Mercados Diarios" (docs/index.html) con los cierres diarios
desde 2015 de los cinco índices y el histórico de reportes diarios.
Todos los cálculos del panel se hacen sobre estos cierres reales.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reporte_mercados as rm  # noqa: E402

PLANTILLA = rm.RAIZ / "scripts" / "panel_plantilla.html"
SALIDA = rm.RAIZ / "docs" / "index.html"
HISTORICO = rm.CARPETA_ARCHIVO / "historico.json"

DESCRIPCIONES = {
    "ibex": ("España", "Las 35 empresas más negociadas de la Bolsa española. Banca, energía y "
                       "construcción pesan mucho en él."),
    "sx5e": ("Zona euro", "Las 50 mayores empresas de los países del euro. Es la referencia de la "
                          "bolsa europea."),
    "spx": ("Estados Unidos", "Las 500 mayores empresas de EE. UU. Es el termómetro de la bolsa "
                              "mundial más seguido."),
    "ndx": ("EE. UU. · tecnología", "Las 100 mayores empresas no financieras del Nasdaq, con mucho "
                                    "peso de la tecnología."),
    "msci": ("Mundial", "Más de 1.300 empresas de 23 países desarrollados, en dólares. Resume cómo "
                        "le va a la bolsa mundial."),
}


def main():
    tickers = [i["yahoo"] for i in rm.INDICES]
    cierres = rm.descargar_cierres(tickers, start="2015-12-01")
    series = {}
    for idx in rm.INDICES:
        s, _ = rm.quitar_intradia(cierres[idx["yahoo"]], idx["zona"])
        if len(s) < 300:
            raise SystemExit(f"Histórico insuficiente para {idx['nombre']}")
        series[idx["clave"]] = {
            "d": [d.strftime("%Y-%m-%d") for d in s.index],
            "v": [round(float(x), 2) for x in s.values],
        }

    historico = json.loads(HISTORICO.read_text(encoding="utf-8")) if HISTORICO.exists() else {}
    datos = {
        "generado": datetime.now(rm.MADRID).strftime("%d/%m/%Y %H:%M"),
        "indices": [{"clave": i["clave"], "nombre": i["nombre"],
                     "region": DESCRIPCIONES[i["clave"]][0], "desc": DESCRIPCIONES[i["clave"]][1]}
                    for i in rm.INDICES],
        "series": series,
        "historico": historico,
        "aviso": rm.AVISO_FINAL,
    }
    html = PLANTILLA.read_text(encoding="utf-8").replace(
        "__DATOS__", json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(html, encoding="utf-8")
    print(f"Panel generado: {SALIDA} ({SALIDA.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
