from configparser import ConfigParser
from datetime import datetime
from elasticsearch import Elasticsearch
import requests
import time
import hashlib
import pathlib
import socket

config = ConfigParser()
config.read('config.ini')
# config.read('../main_config.ini')

URL = config['temp-tele']['temp_url']

TOKEN = config['telegram']['pingbot_key']
CHAT_ID = config['telegram']['pingbot_ph_chat_id']

ES_USER = config['elasticsearch']['username']
ES_PASS = config['elasticsearch']['password']
ES_HOST = config['elasticsearch']['host']

THRESHOLD = int(config['temp-tele']['threshold'])

index_service = config['elasticsearch']['index_service']
service_name = config['temp-tele']['service_name']
service_desc = config['temp-tele']['deskripsi']

es = Elasticsearch(['http://{}:{}@{}'.format(ES_USER, ES_PASS, ES_HOST)])

def get_host():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        host = s.getsockname()[0]
    except: host = '127.0.0.1'
    finally: s.close()
    return host

def get_path():
    path = pathlib.Path(__file__)
    filename = pathlib.os.path.basename(__file__)
    return str(path.absolute())

def heartbeating():
    es.index(index_service, {
        'name': service_name,
        'desc': service_desc,
        'host': get_host(),
        'path': get_path(),
        'status': 1,
        'timestamp': round(datetime.now().timestamp() * 1000)
    }, '_doc', hashlib.md5(service_name.encode()).hexdigest())

def main():
    print("[INFO] Engine started...")
    
    warning = False

    while True:
        temp_req = requests.get(URL)
        if temp_req.status_code == 200:
            temp_res = temp_req.json()
            temp = temp_res['temperature']
            message = ''
            if temp >= THRESHOLD and not warning:
                warning = True
                print('[INFO] Temperature is above 25!')
                message = 'Server temperature above 25 Celcius!'
                requests.get('http://api.telegram.org/bot{}/sendMessage'.format(TOKEN), {
                    'chat_id': CHAT_ID,
                    'text': message,
                    'parse_mode': 'Markdown'
                })
            elif temp < THRESHOLD and warning:
                warning = False
                print('[INFO] Temperature has back to normal.')
                message = 'Server temperature has back to normal.'
                requests.get('http://api.telegram.org/bot{}/sendMessage'.format(TOKEN), {
                    'chat_id': CHAT_ID,
                    'text': message,
                    'parse_mode': 'Markdown'
                })
        else:
            print('[INFO] Temperature request is failed.')
        heartbeating()
        time.sleep(5)

if __name__ == "__main__":
    main()
