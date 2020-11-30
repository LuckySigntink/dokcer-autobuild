from elasticsearch import Elasticsearch
from configparser import ConfigParser
from datetime import datetime
import schedule
import requests
import time
import sys, getopt
import hashlib
import pathlib
import socket

short_opts = 'p:'
long_opts = ['place=']

try:
    arguments, values = getopt.getopt(sys.argv[1:], shortopts=short_opts, longopts=long_opts)
except getopt.error as err:
    print(str(err))
    sys.exit(2)

if len(arguments) < 1:
    print("Argument required!")
    sys.exit(2)

arg_place = [item for item in arguments if item[0] == '-p'] or [item for item in arguments if item[0] == '--place']
key = arg_place[0][1].upper()

if key not in ["BT", "PH", "NDC"]:
    print("Place invalid, please select between PH, BT, or NDC.")
    sys.exit(2)

print("[INFO] Service started...")

config = ConfigParser()
config.read('config.ini')
# config.read('../main_config.ini')

TOKEN = config['telegram']['pingbot_key']
CHAT_ID = {
    "BT": config['telegram']['pingbot_bt_chat_id'],
    "PH": config['telegram']['pingbot_ph_chat_id']
}

es_user = config['elasticsearch']['username']
es_pass = config['elasticsearch']['password']
es_host = config['elasticsearch']['host']

service_names = {
    'BT': config['ipreport']['service_name_bt'],
    'PH': config['ipreport']['service_name_ph']
}
index_heartbeat = config['ipreport']['index_service']
service_name = service_names[key]
service_desc = config['ipreport']['deskripsi']

es = Elasticsearch(['http://{}:{}@{}'.format(es_user, es_pass, es_host)])

def get_host():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        host = s.getsockname()[0]
    except:
        host = '127.0.0.1'
    finally:
        s.close()
    return host

def job():
    count = { 'down': 0, 'up': 0, 'available': 0, 'maintenance': 0 }
    servers = requests.get('http://192.168.99.10:3003/servers/' + key.lower()).json()

    for server in servers:
        if server['srv_status'] == 0:
            count['down'] = count['down'] + 1
        elif server['srv_status'] == 1:
            count['up'] = count['up'] + 1
        elif server['srv_status'] == 2:
            count['available'] = count['available'] + 1
        elif server['srv_status'] == 3:
            count['maintenance'] = count['maintenance'] + 1

    message = '''*REPORT*
    `HEALTHY = {healthy:d}`
    `UNHEALTHY = {unh'ealthy:d}`
    `AVAILABLE = {available:d}`
    '''.format(healthy=count['up'], unhealthy=count['down'], available=count['available'])

    print("[INFO] Sending report...")

    requests.post('https://api.telegram.org/bot' + TOKEN + "/sendMessage", data={
        "chat_id": CHAT_ID[key],
        "text": message,
        "parse_mode": "Markdown"
    })

def export():
    path = pathlib.Path(__file__).parent.absolute()
    filename = pathlib.os.path.basename(__file__)
    full_name = '{}/{}'.format(str(path), str(filename))
    es.index(index=index_heartbeat, doc_type='_doc', id=hashlib.md5(service_name.encode()).hexdigest(), body={
        'name': service_name,
        'status': 1,
        'host': get_host(),
        'path': full_name,
        'desc': service_desc,
        'timestamp': round(datetime.now().timestamp() * 1000)
    })

schedule.every().day.at("10:00").do(job)
schedule.every().day.at("15:00").do(job)
schedule.every().day.at("20:00").do(job)
schedule.every(3).minutes.do(export)

while True:
    schedule.run_pending()
    time.sleep(1)
