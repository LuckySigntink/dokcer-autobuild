from pymongo import MongoClient
from elasticsearch import Elasticsearch
from multiprocessing import Process, Queue
from datetime import datetime
from configparser import ConfigParser
import subprocess
import sys, getopt
import requests
import hashlib
import pathlib
import socket
import re

config = ConfigParser()
config.read('config.ini')
config.read('../main_config.ini')

es_user = config['elasticsearch']['username']
es_pass = config['elasticsearch']['password']
es_host = config['elasticsearch']['host']

mongo_host = config['mongodb']['host']
mongo_user = config['mongodb']['auth'].split('/')[0]
mongo_pass = config['mongodb']['auth'].split('/')[1]

alert_host = config['pingbot']['alert_host']
index_heartbeat = config['elasticsearch']['index_service']

TOKEN = config['telegram']['pingbot_key']
CHAT_ID = {"BT": config['telegram']['pingbot_bt_chat_id'], "PH": config['telegram']['pingbot_ph_chat_id']}

# old mongo connection
# client = MongoClient(config['mongodb']['host'], int(config['mongodb']['port']))

client = MongoClient('mongodb://{}:{}@{}'.format(mongo_user, mongo_pass, mongo_host))
es = Elasticsearch(["http://{}:{}@{}".format(config['elasticsearch']['username'], config['elasticsearch']['password'], config['elasticsearch']['host'])])

ip_blacklist = ["192.168.180.", " "]
db = client[config['mongodb']['db']]
servers = db['infra_server']
location = db['infra_location']
racks = db['infra_rack']

service_name = {
    'BT': config['pingbot']['service_name_bt'],
    'PH': config['pingbot']['service_name_ph']
}
service_desc = config['pingbot']['deskripsi']

def alerting(host: str, rack: str, place: str):
    res = requests.post('http://{}/publish'.format(alert_host), { 'content': { 'host': host, 'rack': rack, 'place': place } })
    if res.status_code != 200: print("Alerting to {} failed.".format(alert_host))

def get_host():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        host = s.getsockname()
    except: host = '127.0.0.1'
    finally: s.close()
    return host[0]

def get_path():
    path = pathlib.Path(__file__)
    filename = pathlib.os.path.basename(__file__)
    return '{}'.format(str(path.absolute()))

def ping_check(ip):
    res = subprocess.call(['ping', '-c', '4', ip], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if res == 0:
        status = 1
    else:
        status = 0
    return status

def procy(ip, ping_up, ping_down):
    status = ping_check(ip)
    if status == 1:
        ping_up.put(ip)
    else:
        print( ip + " disconnected")
        ping_down.put(ip)

def gettime():
    time = datetime.now()
    year = str(time.year)
    if time.month < 9:
        month = "0" + str(time.month)
    else:
        month = str(time.month)
    return year + month

def check_ip(ip):
    ip_pattern = "\d{1,3}[.]\d{1,3}[.]\d{1,3}[.]\d{1,3}"
    if re.match(ip_pattern, ip): return True
    else: return False

short_options = 'p:'
long_options = ['place=']

try:
    arguments, values = getopt.getopt(sys.argv[1:], shortopts=short_options, longopts=long_options)
except getopt.error as err:
    print(str(err))
    sys.exit(2)

if len(arguments) < 1:
    print("Argument required!")
    sys.exit(2)

arg_place = [item for item in arguments if item[0] == '-p'] or [item for item in arguments if item[0] == '--place']
key = arg_place[0][1].upper()

if key not in ['BT', 'PH', 'NDC']:
    print("Place invalid, please select between PH, BT, or NDC.")
    sys.exit(2)

print("[INFO] Service starting...")

locs = {}
for loc in location.find({}):
    locs[loc['loc_code']] = {"loc_id": loc['_id'], "loc_code": loc['loc_code'], "loc_name": loc['loc_name']}

while True:
    server_active = servers.find({ "loc_id": locs[key]['loc_id'], '$or': [{ "srv_status": 0 }, { "srv_status": 1 }] })

    last_down_q = servers.find({ 'srv_status': 0 })
    last_down = []
    for server in last_down_q:
        last_down.append(server['srv_ip'])

    counts = {}
    try:
        f = open("count_" + key + ".txt", "r")
        for line in f.readlines():
            query = line.split(" ")
            try:
                counts[query[0].strip()] = int(query[1])
            except IndexError:
                counts[query[0].strip()] = 0
        f.close()
    except FileNotFoundError:
        with open("count_" + key + ".txt", "w"): pass

    ping_down = Queue()
    ping_up = Queue()

    pinger = []

    for server in server_active:
        if server['srv_ip'] in ip_blacklist:
            continue

        if not check_ip(server['srv_ip']):
            print("[ERROR] IP {} is not valid!".format(server['srv_ip']))
            continue

        p = Process(target=procy, args=(server['srv_ip'], ping_up, ping_down))
        p.start()
        pinger.append(p)

    for process in pinger:
        process.join()

    new_down = []
    for n in range(ping_down.qsize()):
        new_down.append(ping_down.get())

    new_up = []
    for n in range(ping_up.qsize()):
        new_up.append(ping_up.get())

    changed_down = list(set(new_down).difference(set(last_down)))
    changed_up = list(set(new_up).intersection(set(last_down)))

    print("down: " + str(changed_down) + " up: " + str(changed_up))
    print(counts)

    # changed down with counts
    changed_down_counted = []
    for ip in changed_down:
        if counts.get(ip) == 3:
            changed_down_counted.append(ip)

    # logging for newly down server
    for ip in changed_down_counted:
        servers.update_one({ 'srv_ip': ip }, { '$set': { 'srv_status': 0 } })
        data = servers.find_one({ 'srv_ip': ip })
        es.index(index="monitoring-server-" + gettime(), doc_type="_doc", body={
            "ip": ip,
            "place_id": data['loc_id'],
            "tags_place": location.find_one({"_id": data['loc_id']}).get('loc_code'),
            "svrtype": data['srv_type'] or "NA",
            "svrrack": racks.find_one({"_id": data['rack_id']}).get('rack_code'),
            "timestamp": round(datetime.now().timestamp()) * 1000
        })

    # logging for newly up server
    for ip in changed_up:
        servers.update_one({ 'srv_ip': ip }, { '$set': { 'srv_status': 1 } })

    # message to telegram
    if len(changed_down_counted) + len(changed_up) > 0:
        message = ""
        if len(changed_down_counted) > 0:
            message += "*DOWN ALERT*\n"
            for ip in changed_down_counted:
                data_server = servers.find_one({'srv_ip': ip})
                rack = racks.find_one({'_id': data_server['rack_id']}).get('rack_name')
                message += "`" + ip + " " + rack + "`\n"
                alerting(ip, rack, key)
        if len(changed_down_counted) > 0 and len(changed_up) > 0:
            message += "===================\n"
        if len(changed_up) > 0:
            message += "*UP ALERT*\n"
            for ip in changed_up:
                data_server = servers.find_one({'srv_ip': ip})
                rack = racks.find_one({'_id': data_server['rack_id']}).get('rack_name')
                message += "`" + ip + " " + rack + "`\n";
        message += "\n*REPORT*\n"
        message += "`HEALTHY = " + str(len(new_up)) + "`\n"
        message += "`UNHEALTHY = " + str(len(new_down)) + "`\n"
        message += "`AVAILABLE = " + str(servers.find({'srv_status': 2}).count()) + "`\n"
        # send message to telegram
        requests.post("https://api.telegram.org/bot" + TOKEN + "/sendMessage", data={
            "chat_id": CHAT_ID[key],
            "text": message,
            "parse_mode": "Markdown"
        })

    f = open("count_" + key + ".txt", "w")
    for ip in new_down:
        count_before = counts.get(ip)
        if count_before or count_before == 0:
            count = str(count_before + 1)
        else:
            count = "0"
        if (check_ip(ip)):
            f.write(ip + " " + count + "\n")
        else:
            print("[ERRRRROR] IP {} IS NOT VALID!!1!1!!!11!".format(ip))
    f.close()

    # Log heartbeat
    print("[INFO] Logging heartbeat to elasticsearch...")
    if key == 'PH': service_name = "server-pingbot-ph"
    else: service_name = 'server-pingbot-bt'
    es.index(index=index_heartbeat, doc_type="_doc", id=hashlib.md5(service_name.encode()).hexdigest(), body={
        'name': service_name,
        'status': 1,
        'host': get_host(),
        'path': get_path(),
        'desc': service_desc,
        'timestamp': round(datetime.now().timestamp()) * 1000
    })
