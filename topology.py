import re
import json
import datetime
import os

from pathlib import Path
from nornir import InitNornir
from nornir.core.task import Result
from nornir.plugins.tasks import networking
from collections import defaultdict
from shutil import copy2

TOPOLOGY_CURRENT = 'data/topology.json'
TOPOLOGY_PREV = 'data/topology_prev.json'

REMOVED_COLOR = 'red'
ADDED_COLOR = 'green'
NOT_CHANGED_COLOR = 'black'


def get_lldp_neighbors(task):
    r = task.run(
        task=networking.netmiko_send_command,
        command_string='show lldp neighbors'
    )

    task.host['lldp_neig'] = r.result


def parse_lldp_neighbors(task, nodes):
    neighbor_data = []

    regex = r"^(?P<Hostname>\S+)\s+(?P<LocalInterface>\S+)\s+\d+\s+(?:(?P<Capability>\S+))?\s+(?P<NeighborInterface>\S+)$"
    matches = re.finditer(regex, task.host['lldp_neig'], re.MULTILINE)
    for match in matches:
        # if R in Capability that is router else switch
        router = True if match.group('Capability') and 'R' in match.group('Capability') else False

        # output in my lab shows neighbors thought subinterfaces, looks like ios bug
        # skip it
        if '.' in match.group('NeighborInterface'):
            continue

        # skip neighbors that are outside of our inventory
        if match.group('Hostname') not in nodes:
           continue

        item = {
            'Hostname': match.group('Hostname'),
            'LocalInterface': match.group('LocalInterface'),
            'NeighborInterface': match.group('NeighborInterface'),
            'Router': router
        }

        neighbor_data.append(item)

    task.host['neighbor_data'] = neighbor_data


def collect_neighbors(task, nodes):
    print(f'start collection on host {task.host.hostname} {task.host.port}')

    task.run(
        task=get_lldp_neighbors,
        name='Getting Neighbor information'
    )

    task.run(
        task=parse_lldp_neighbors,
        name='Data parser',
        nodes=nodes
    )

    print(f'end collection on host {task.host.hostname} {task.host.port}')

    return Result(result=task.host['neighbor_data'], host=task.host)


def prepare_topology_data(nodes, data):
    routers = set()
    failed_hosts = set()
    links = defaultdict(tuple)

    topology_data = defaultdict(list)

    # iterate over result
    for node in data:
        # check failed result
        if data[node].failed:
            failed_hosts.add(node)
            continue

        for neighbor in data[node].result:
            # if neighbor is router insert in name to set
            if neighbor['Router']:
                routers.add(neighbor['Hostname'])

            # deduplicate links
            if links.get((neighbor['Hostname'], neighbor['NeighborInterface'])):
                continue

            links[(node, neighbor['LocalInterface'])] = (neighbor['Hostname'], neighbor['NeighborInterface'])

    for node in nodes:
        # check for failed
        if node in failed_hosts:
            continue

        type_ = 'Router' if node in routers else 'Switch'
        topology_data['nodes'].append({'Hostname': node, 'Type': type_})

    for (source, destination) in links.items():
        (source_hostname, source_interface) = source
        (destination_hostname, destination_interface) = destination

        topology_data['links'].append({
                'Source': source_hostname,
                'SourceInterface': source_interface,
                'Destination': destination_hostname,
                'DestinationInterface': destination_interface
            }
        )

    return topology_data


def archive_topology():
    #copy current topology to prev
    if os.path.exists(TOPOLOGY_CURRENT):
        copy2(TOPOLOGY_CURRENT, TOPOLOGY_PREV)


def write_topology(data):
    # create dir if it not exists
    Path("data/archive").mkdir(parents=True, exist_ok=True)

    out = open(TOPOLOGY_CURRENT, 'w')
    out.write(json.dumps(data, indent=4, sort_keys=True))
    out.close()

    print(f'topology data has written to file')

    timestamp = datetime.datetime.now().strftime("%Y_%m_%d_%H_%M")
    copy2(TOPOLOGY_CURRENT, f'data/archive/topology_{timestamp}.json')

    print(f'topology data has been saved')


def compare_nodes(source, destination, with_delta, delta_color):
    for node in destination['nodes']:
        # check exist in source
        if not any(d['Hostname'] == node['Hostname'] for d in source['nodes']):
            # add it to delta data if color is not delta_color
            if node.get('color') != delta_color:
                with_delta['nodes'].append({'Hostname': node['Hostname'], 'Type': node['Type'], 'color': delta_color})
        else:
            # avoid duplicates in nodes list
            if not any(d['Hostname'] == node['Hostname'] for d in with_delta['nodes']):
                with_delta['nodes'].append({
                    'Hostname': node['Hostname'],
                    'Type': node['Type'],
                    'color': NOT_CHANGED_COLOR
                })


def compare_links(source, destination, with_delta, delta_color):
    for link in destination['links']:
        # check exist in source
        if not any(
                d['Source'] == link['Source'] and
                d['Destination'] == link['Destination'] and
                d['SourceInterface'] == link['SourceInterface'] and
                d['DestinationInterface'] == link['DestinationInterface']
                for d in source['links']
        ):
            # add it to delta data if color is not delta_color
            if link.get('color') != delta_color:
                with_delta['links'].append({
                    'Source': link['Source'],
                    'Destination': link['Destination'],
                    'SourceInterface': link['SourceInterface'],
                    'DestinationInterface': link['DestinationInterface'],
                    'color': delta_color
                })
        else:
            if not any(
                    d['Source'] == link['Source'] and
                    d['Destination'] == link['Destination'] and
                    d['SourceInterface'] == link['SourceInterface'] and
                    d['DestinationInterface'] == link['DestinationInterface']
                    for d in with_delta['links']
            ):
                with_delta['links'].append({
                    'Source': link['Source'],
                    'Destination': link['Destination'],
                    'SourceInterface': link['SourceInterface'],
                    'DestinationInterface': link['DestinationInterface'],
                    'color': NOT_CHANGED_COLOR
                })


def compare_topology(current):
    try:
        prev_file = open(TOPOLOGY_PREV)
        prev = json.load(prev_file)
    except (OSError, IOError) as e:
        prev = {'links': [], 'nodes': []}

    with_delta = defaultdict(list)

    # let's find new nodes and mark it with green
    compare_nodes(source=prev, destination=current, with_delta=with_delta, delta_color=ADDED_COLOR)
    # let's find new nodes and mark it with red
    compare_nodes(source=current, destination=prev, with_delta=with_delta, delta_color=REMOVED_COLOR)

    # let's find new nodes and mark it with green
    compare_links(source=prev, destination=current, with_delta=with_delta, delta_color=ADDED_COLOR)
    # let's find new nodes and mark it with red
    compare_links(source=current, destination=prev, with_delta=with_delta, delta_color=REMOVED_COLOR)

    return with_delta


def validate_data(data):
    key_list = ('nodes', 'links')
    for key in key_list:
        if not data.get(key):
            return False

    return True


def generate_topology_data():
    nr = InitNornir(config_file='config.yaml')

    nodes = [host.name for host in nr.inventory.hosts.values()]

    result = nr.run(task=collect_neighbors, nodes=nodes)

    topology = prepare_topology_data(nodes=nodes, data=result)

    if not validate_data(data=topology):
        return

    archive_topology()

    topology = compare_topology(current=topology)

    write_topology(data=topology)
