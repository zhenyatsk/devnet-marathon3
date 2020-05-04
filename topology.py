import re
import json
import datetime

from nornir import InitNornir
from nornir.core.task import Result
from nornir.plugins.tasks import networking
from collections import defaultdict
from shutil import copy2


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


def write_topology(data):
    out = open('data/topology.json', 'w')
    out.write(json.dumps(data, indent=4, sort_keys=True))
    out.close()

    print(f'topology data has written to file')

    timestamp = datetime.datetime.now().strftime("%Y_%m_%d_%H_%M")
    copy2('data/topology.json', f'data/topology_{timestamp}.json')

    print(f'topology data has been saved')


def generate_topology_data():
    nr = InitNornir(config_file='config.yaml')

    nodes = [host.name for host in nr.inventory.hosts.values()]

    result = nr.run(task=collect_neighbors, nodes=nodes)

    topology = prepare_topology_data(nodes=nodes, data=result)

    write_topology(data=topology)


if __name__ == "__main__":
    generate_topology_data()
