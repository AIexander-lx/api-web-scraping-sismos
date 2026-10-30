import json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
import requests

# La página https://ultimosismo.igp.gob.pe/productos/reportes-sismicos es una app web (Angular):
# la tabla se dibuja con JavaScript a partir de este endpoint JSON que la propia página consulta.
# Por eso se hace el scraping directamente sobre ese endpoint (ver pestaña Network del navegador).
URL_BASE = "https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/{anio}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CS2032-WebScraping/1.0)"}
NOMBRE_TABLA = "TablaSismosIGP"
CANTIDAD = 10


def obtener_sismos(anio):
    response = requests.get(URL_BASE.format(anio=anio), headers=HEADERS, timeout=20)
    response.raise_for_status()
    datos = response.json()
    return datos if isinstance(datos, list) else datos.get("data", [])


def clave_orden(sismo):
    # Ordena por fecha y hora local del sismo (más reciente primero)
    fecha = str(sismo.get("fecha_local") or sismo.get("fecha_utc") or sismo.get("createdAt") or "")
    hora = str(sismo.get("hora_local") or sismo.get("hora_utc") or "")
    return (fecha[:10], hora)


def lambda_handler(event, context):
    try:
        # 1. Obtener los sismos del año actual (hora de Perú, UTC-5)
        anio = datetime.now(timezone(timedelta(hours=-5))).year
        sismos = obtener_sismos(anio)
        # Si a inicios de año hay menos de 10, se completan con el año anterior
        if len(sismos) < CANTIDAD:
            sismos += obtener_sismos(anio - 1)

        if not sismos:
            return {'statusCode': 404, 'body': 'No se encontraron sismos en la web del IGP'}

        # 2. Quedarse con los 10 más recientes
        ultimos = sorted(sismos, key=clave_orden, reverse=True)[:CANTIDAD]

        # 3. Guardar en DynamoDB
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(NOMBRE_TABLA)

        # Eliminar los registros anteriores
        scan = table.scan()
        with table.batch_writer() as batch:
            for each in scan['Items']:
                batch.delete_item(Key={'id': each['id']})

        # Insertar los 10 últimos sismos
        registros = []
        for i, sismo in enumerate(ultimos, start=1):
            # DynamoDB no acepta float: se convierten a Decimal
            item = json.loads(json.dumps(sismo), parse_float=Decimal)
            item['id'] = str(uuid.uuid4())
            item['#'] = i
            table.put_item(Item=item)
            registros.append(item)

        # 4. Retornar el resultado como JSON
        return {
            'statusCode': 200,
            'cantidad': len(registros),
            'body': json.loads(json.dumps(registros, default=str))
        }

    except requests.RequestException as e:
        return {'statusCode': 502, 'body': f'Error al acceder a la web del IGP: {e}'}
