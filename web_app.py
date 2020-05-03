from flask import Flask, send_from_directory, redirect
import os.path

from topology import generate_topology_data

TOPOLOGY_DATA = 'data/topology.json'

app = Flask(__name__, static_url_path='')

@app.route('/static/<path:path>')
def send_js(path):
    return send_from_directory('static', path)

@app.route('/refresh_data')
def refresh_data():
    generate_topology_data()
    return 'done'

@app.route('/data/topology.json')
def topology():
    # if file not exist lets collect it
    if not os.path.isfile(TOPOLOGY_DATA):
        generate_topology_data()
    f = open(TOPOLOGY_DATA, "r")
    return f.read()

@app.route('/')
def index():
    return redirect('static/index.html', code=302)

if __name__ == '__main__':
    app.run()
