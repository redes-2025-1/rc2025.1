# -*- coding: utf-8 -*-

import csv
import json
import threading
import time
from argparse import ArgumentParser
from collections import defaultdict

import requests
from flask import Flask, jsonify, request

# --- Funções Auxiliares para Sumarização de Rotas (Corrigidas) ---

def ip_to_int(ip_str):
    """Converte uma string de IP (v4) para um inteiro de 32 bits."""
    parts = ip_str.split('.')
    return (int(parts[0]) << 24) + (int(parts[1]) << 16) + (int(parts[2]) << 8) + int(parts[3])

def int_to_ip(ip_int):
    """Converte um inteiro de 32 bits de volta para uma string de IP."""
    return f"{(ip_int >> 24) & 0xFF}.{(ip_int >> 16) & 0xFF}.{(ip_int >> 8) & 0xFF}.{ip_int & 0xFF}"

def _find_supernet_for_group(networks):
    """
    Verifica se duas redes são adjacentes e podem ser sumarizadas.
    Retorna a nova super-rede ou None se não for possível.
    """
    if not networks or len(networks) <= 1:
        return None
    
    try:
        ip_ints = [ip_to_int(net.split('/')[0]) for net in networks]
        prefix = int(networks[0].split('/')[1])
        
        min_ip = min(ip_ints)
        max_ip = max(ip_ints)
        
        if min_ip == max_ip:
            return networks[0]
        
        xor_val = min_ip ^ max_ip
        common_bits = 32 - xor_val.bit_length()
        new_prefix = min(prefix, common_bits)

        if new_prefix <= 8 and new_prefix < common_bits:
            return None
        
        mask = (0xFFFFFFFF << (32 - new_prefix)) & 0xFFFFFFFF
        supernet_int = min_ip & mask
        
        return f"{int_to_ip(supernet_int)}/{new_prefix}"

    except (ValueError, IndexError):
        return None

class Router:
    """
    Representa um roteador que executa o algoritmo de Vetor de Distância.
    """

    def __init__(self, my_address, neighbors, my_network, update_interval=1):
        """
        Inicializa o roteador.

        :param my_address: O endereço (ip:porta) deste roteador.
        :param neighbors: Um dicionário contendo os vizinhos diretos e o custo do link.
                          Ex: {'127.0.0.1:5001': 5, '127.0.0.1:5002': 10}
        :param my_network: A rede que este roteador administra diretamente.
                           Ex: '10.0.1.0/24'
        :param update_interval: O intervalo em segundos para enviar atualizações, o tempo que o roteador espera 
                                antes de enviar atualizações para os vizinhos.        """
        self.my_address = my_address
        self.neighbors = neighbors
        self.my_network = my_network
        self.update_interval = update_interval
        self.routing_table = {}
        self.lock = threading.Lock()

        # TODO: Este é o local para criar e inicializar sua tabela de roteamento.
        #
        # 1. Crie a estrutura de dados para a tabela de roteamento. Um dicionário é
        #    uma ótima escolha, onde as chaves são as redes de destino (ex: '10.0.1.0/24')
        #    e os valores são outro dicionário contendo 'cost' e 'next_hop'.
        #    Ex: {'10.0.1.0/24': {'cost': 0, 'next_hop': '10.0.1.0/24'}}
        #
        # 2. Adicione a rota para a rede que este roteador administra diretamente
        #    (a rede em 'self.my_network'). O custo para uma rede diretamente
        #    conectada é 0, e o 'next_hop' pode ser a própria rede ou o endereço do roteador.
        #
        # 3. Adicione as rotas para seus vizinhos diretos, usando o dicionário
        #    'self.neighbors'. Para cada vizinho, o 'cost' é o custo do link direto
        #    e o 'next_hop' é o endereço do próprio vizinho.
        self.routing_table[self.my_network] = {"cost": 0, "next_hop": self.my_network}
        for neighbor_addr, cost in self.neighbors.items():
            self.routing_table[neighbor_addr] = {"cost": cost, "next_hop": neighbor_addr}

        print("Tabela de roteamento inicial:")
        print(json.dumps(self.routing_table, indent=4))

        # Inicia o processo de atualização periódica em uma thread separada
        self._start_periodic_updates()

    def _start_periodic_updates(self):
        """Inicia uma thread para enviar atualizações periodicamente."""
        thread = threading.Thread(target=self._periodic_update_loop)
        thread.daemon = True
        thread.start()

    def _periodic_update_loop(self):
        """Loop que envia atualizações de roteamento em intervalos regulares."""
        while True:
            time.sleep(self.update_interval)
            print(f"[{time.ctime()}] Enviando atualizações periódicas para os vizinhos...")
            try:
                self.send_updates_to_neighbors()
            except Exception as e:
                print(f"Erro durante a atualização periódida: {e}")

    def _summarize_routes(self, routes_to_summarize):
        """
        Aplica a lógica de sumarização de forma iterativa para encontrar a melhor agregação.
        """
        summarized_table = {}
        routes_by_hop = defaultdict(list)

        # 1. Agrupa todas as rotas de rede pelo seu 'next_hop'
        for network, data in routes_to_summarize.items():
            if "/" in network:
                routes_by_hop[data['next_hop']].append(network)
            else:
                summarized_table[network] = data
        
        # 2. Tenta sumarizar cada grupo de redes
        for next_hop, networks in routes_by_hop.items():
            if len(networks) > 1:
                supernet = _find_supernet_for_group(networks)
                if supernet:

                    print(f"Rotas via {next_hop} {networks} foram sumarizadas para - {supernet}")
                    max_cost = max(routes_to_summarize[net]['cost'] for net in networks)
                    summarized_table[supernet] = {'cost': max_cost, 'next_hop': next_hop}
                else:
                    for net in networks:
                        summarized_table[net] = routes_to_summarize[net]
            else:
                if networks:
                    net = networks[0]
                    summarized_table[net] = routes_to_summarize[net]

        return summarized_table

    def send_updates_to_neighbors(self):
        """
        Envia a tabela de roteamento (potencialmente sumarizada) para todos os vizinhos.
        """
        with self.lock:
            if not self.routing_table:
                return
            
            # TODO: O código abaixo envia a tabela de roteamento *diretamente*.
            #
            # ESTE TRECHO DEVE SER CHAMAADO APOS A SUMARIZAÇÃO.
            #
            # dica:
            # 1. CRIE UMA CÓPIA da `self.routing_table` NÃO ALTERE ESTA VALOR.
            # 2. IMPLEMENTE A LÓGICA DE SUMARIZAÇÃO nesta cópia.
            # 3. ENVIE A CÓPIA SUMARIZADA no payload, em vez da tabela original.

            for neighbor_address in self.neighbors:
                
                table_for_neighbor = {}
                for network, info in self.routing_table.items():
                    if info['next_hop'] != neighbor_address:
                        table_for_neighbor[network] = info
                
                summarized_table = self._summarize_routes(table_for_neighbor)

                payload = {
                    "sender_address": self.my_address,
                    "routing_table": summarized_table
                }

                url = f'http://{neighbor_address}/receive_update'
                try:
                    print(f"Enviando tabela para {neighbor_address}")
                    requests.post(url, json=payload, timeout=2)
                except requests.exceptions.RequestException as e:
                    print(f"Não foi possível conectar ao vizinho {neighbor_address}. Erro: {e}")

    def update_routing_table(self, sender_address, sender_table):
        with self.lock:
            updated = False
            
            cost_to_sender = self.neighbors.get(sender_address)
            if cost_to_sender is None:
                return

            for network, info in sender_table.items():
                new_cost = cost_to_sender + info["cost"]

                current_entry = self.routing_table.get(network)

                if current_entry is None:
                    self.routing_table[network] = {"cost": new_cost, "next_hop": sender_address}
                    updated = True
                else:
                    current_cost = current_entry["cost"]
                    current_hop = current_entry["next_hop"]

                    if new_cost < current_cost:
                        self.routing_table[network] = {"cost": new_cost, "next_hop": sender_address}
                        updated = True
                    elif current_hop == sender_address and current_cost != new_cost:
                        self.routing_table[network]["cost"] = new_cost
                        updated = True
            
            if updated:
                print("\nRouting Table Updated:")
                print(json.dumps(self.routing_table, indent=4))

# --- API Endpoints ---
# Instância do Flask e do Roteador (serão inicializadas no main)
app = Flask(__name__)
router_instance = None

@app.route('/routes', methods=['GET'])
def get_routes():
    """Endpoint para visualizar a tabela de roteamento atual."""
    # TODO: Aluno! Este endpoint está parcialmente implementado para ajudar na depuração.
    # Você pode mantê-lo como está ou customizá-lo se desejar.
    # - mantenha o routing_table como parte da resposta JSON.
    if router_instance:
        with router_instance.lock:
            return jsonify({
                "message": "Não implementado!.",
                "vizinhos" : router_instance.neighbors,
                "my_network": router_instance.my_network,
                "my_address": router_instance.my_address,
                "update_interval": router_instance.update_interval,
                "routing_table": router_instance.routing_table
            })
    return jsonify({"error": "Roteador não inicializado"}), 500

@app.route('/receive_update', methods=['POST'])
def receive_update():
    """Endpoint que recebe atualizações de roteamento de um vizinho."""
    if not request.json:
        return jsonify({"error": "Invalid request"}), 400

    update_data = request.json
    sender_address = update_data.get("sender_address")
    sender_table = update_data.get("routing_table")

    if not sender_address or not isinstance(sender_table, dict):
        return jsonify({"error": "Missing sender_address or routing_table"}), 400

    print(f"Recebida atualização de {sender_address}:")
    print(json.dumps(sender_table, indent=4))
    
    # TODO: Implemente a lógica de Bellman-Ford aqui.
    #
    # 1. Verifique se o remetente é um vizinho conhecido.
    # 2. Obtenha o custo do link direto para este vizinho a partir de `router_instance.neighbors`.
    # 3. Itere sobre cada rota (`network`, `info`) na `sender_table` recebida.
    # 4. Calcule o novo custo para chegar à `network`:
    #    novo_custo = custo_do_link_direto + info['cost']
    # 5. Verifique sua própria tabela de roteamento:
    #    a. Se você não conhece a `network`, adicione-a à sua tabela com o
    #       `novo_custo` e o `next_hop` sendo o `sender_address`.
    #    b. Se você já conhece a `network`, verifique se o `novo_custo` é menor
    #       que o custo que você já tem. Se for, atualize sua tabela com o
    #       novo custo e o novo `next_hop`.
    #    c. (Opcional, mas importante para robustez): Se o `next_hop` para uma rota
    #       for o `sender_address`, você deve sempre atualizar o custo, mesmo que
    #       seja maior (isso ajuda a propagar notícias de links quebrados).
    #
    # 6. Mantenha um registro se sua tabela mudou ou não. Se mudou, talvez seja
    #    uma boa ideia imprimir a nova tabela no console.
    if router_instance:
        router_instance.update_routing_table(sender_address, sender_table)

    return jsonify({"status": "success", "message": "Update received"}), 200

if __name__ == '__main__':
    parser = ArgumentParser(description="Simulador de Roteador com Vetor de Distância")
    parser.add_argument('-p', '--port', type=int, default=5000, help="Porta para executar o roteador.")
    parser.add_argument('-f', '--file', type=str, required=True, help="Arquivo CSV de configuração de vizinhos.")
    parser.add_argument('--network', type=str, required=True, help="Rede administrada por este roteador (ex: 10.0.1.0/24).")
    parser.add_argument('--interval', type=int, default=10, help="Intervalo de atualização periódica em segundos.")
    args = parser.parse_args()

    # Leitura do arquivo de configuração de vizinhos
    neighbors_config = {}
    try:
        with open(args.file, mode='r') as infile:
            reader = csv.DictReader(infile)
            for row in reader:
                neighbors_config[row['vizinho']] = int(row['custo'])
    except FileNotFoundError:
        print(f"Erro: Arquivo de configuração '{args.file}' não encontrado.")
        exit(1)
    except (KeyError, ValueError) as e:
        print(f"Erro no formato do arquivo CSV: {e}. Verifique as colunas 'vizinho' e 'custo'.")
        exit(1)

    my_full_address = f"127.0.0.1:{args.port}"
    print("--- Iniciando Roteador ---")
    print(f"Endereço: {my_full_address}")
    print(f"Rede Local: {args.network}")
    print(f"Vizinhos Diretos: {neighbors_config}")
    print(f"Intervalo de Atualização: {args.interval}s")
    print("--------------------------")

    router_instance = Router(
        my_address=my_full_address,
        neighbors=neighbors_config,
        my_network=args.network,
        update_interval=args.interval
    )

    # Inicia o servidor Flask
    app.run(host='0.0.0.0', port=args.port, debug=False)