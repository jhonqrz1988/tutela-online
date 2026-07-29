"""Poblar la tabla citas_legales con las referencias base para tutelas de salud."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.cita_legal import CitaLegal


CITAS_BASE_SALUD = [
    {
        "tipo": "constitucion",
        "referencia": "Art. 86 Constitucion Politica de Colombia",
        "referencia_normalizada": "art 86 constitucion politica de colombia",
        "titulo_corto": "Art. 86 C.P. - Accion de Tutela",
        "texto_resumen": "Toda persona tendra accion de tutela para reclamar ante los jueces la proteccion inmediata de sus derechos fundamentales.",
        "url_fuente": "https://www.constitucioncolombia.com/titulo-2/capitulo-4/articulo-86",
        "aplica_a": "salud",
    },
    {
        "tipo": "constitucion",
        "referencia": "Art. 49 Constitucion Politica de Colombia",
        "referencia_normalizada": "art 49 constitucion politica de colombia",
        "titulo_corto": "Art. 49 C.P. - Derecho a la Salud",
        "texto_resumen": "La atencion de la salud y el saneamiento ambiental son servicios publicos a cargo del Estado. Se garantiza a todas las personas el acceso a los servicios de promocion, proteccion y recuperacion de la salud.",
        "url_fuente": "https://www.constitucioncolombia.com/titulo-2/capitulo-2/articulo-49",
        "aplica_a": "salud",
    },
    {
        "tipo": "constitucion",
        "referencia": "Art. 48 Constitucion Politica de Colombia",
        "referencia_normalizada": "art 48 constitucion politica de colombia",
        "titulo_corto": "Art. 48 C.P. - Seguridad Social",
        "texto_resumen": "La Seguridad Social es un servicio publico de caracter obligatorio que se prestara bajo la direccion, coordinacion y control del Estado.",
        "url_fuente": "https://www.constitucioncolombia.com/titulo-2/capitulo-2/articulo-48",
        "aplica_a": "salud",
    },
    {
        "tipo": "constitucion",
        "referencia": "Art. 11 Constitucion Politica de Colombia",
        "referencia_normalizada": "art 11 constitucion politica de colombia",
        "titulo_corto": "Art. 11 C.P. - Derecho a la Vida",
        "texto_resumen": "El derecho a la vida es inviolable. No habra pena de muerte.",
        "url_fuente": "https://www.constitucioncolombia.com/titulo-2/capitulo-1/articulo-11",
        "aplica_a": "salud",
    },
    {
        "tipo": "constitucion",
        "referencia": "Art. 2 Constitucion Politica de Colombia",
        "referencia_normalizada": "art 2 constitucion politica de colombia",
        "titulo_corto": "Art. 2 C.P. - Fines del Estado",
        "texto_resumen": "Son fines esenciales del Estado: servir a la comunidad, promover la prosperidad general y garantizar la efectividad de los principios, derechos y deberes consagrados en la Constitucion.",
        "url_fuente": "https://www.constitucioncolombia.com/titulo-1/articulo-2",
        "aplica_a": "salud",
    },
    {
        "tipo": "decreto",
        "referencia": "Decreto 2591 de 1991",
        "referencia_normalizada": "decreto 2591 de 1991",
        "titulo_corto": "Decreto 2591/1991 - Reglamentacion Tutela",
        "texto_resumen": "Por el cual se reglamenta la accion de tutela consagrada en el articulo 86 de la Constitucion Politica.",
        "url_fuente": "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=1388",
        "aplica_a": "salud",
    },
    {
        "tipo": "ley",
        "referencia": "Ley 1751 de 2015",
        "referencia_normalizada": "ley 1751 de 2015",
        "titulo_corto": "Ley 1751/2015 - Ley Estatutaria de Salud",
        "texto_resumen": "Por medio de la cual se regula el derecho fundamental a la salud y se dictan otras disposiciones. La salud es un derecho fundamental autonomo e irrenunciable.",
        "url_fuente": "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=64923",
        "aplica_a": "salud",
    },
    {
        "tipo": "sentencia",
        "referencia": "Sentencia T-760 de 2008",
        "referencia_normalizada": "t 760 de 2008",
        "titulo_corto": "T-760/2008 - Derecho a la Salud",
        "texto_resumen": "Sentencia de la Corte Constitucional que unifico la jurisprudencia en materia de salud. Establecio que el derecho a la salud es fundamental y exigible por via de tutela. Ordeno la regulacion integral del sistema de salud.",
        "url_fuente": "https://www.corteconstitucional.gov.co/relatoria/2008/T-760-08.htm",
        "aplica_a": "salud",
    },
    {
        "tipo": "sentencia",
        "referencia": "Sentencia T-859 de 2003",
        "referencia_normalizada": "t 859 de 2003",
        "titulo_corto": "T-859/2003 - Medicamentos no POS",
        "texto_resumen": "La Corte Constitucional establecio que las EPS deben suministrar medicamentos no incluidos en el POS cuando sean recetados por el medico tratante y el paciente no tenga capacidad de pago.",
        "url_fuente": "https://www.corteconstitucional.gov.co/relatoria/2003/T-859-03.htm",
        "aplica_a": "salud",
    },
    {
        "tipo": "decreto",
        "referencia": "Decreto 780 de 2016",
        "referencia_normalizada": "decreto 780 de 2016",
        "titulo_corto": "Decreto 780/2016 - Sistema de Salud",
        "texto_resumen": "Por medio del cual se expide el Decreto Unico Reglamentario del Sector Salud y Proteccion Social.",
        "url_fuente": "https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=70056",
        "aplica_a": "salud",
    },
]


def seed_citas():
    init_db()
    session = SessionLocal()
    try:
        existing = session.query(CitaLegal).count()
        if existing > 0:
            print(f"Ya existen {existing} citas en la base de datos. Omitiendo seed.")
            return

        for cita in CITAS_BASE_SALUD:
            session.add(CitaLegal(**cita))
        session.commit()
        print(f"Seed completado: {len(CITAS_BASE_SALUD)} citas legales insertadas.")
    finally:
        session.close()


if __name__ == "__main__":
    seed_citas()