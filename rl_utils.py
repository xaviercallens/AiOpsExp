import requests
import time
import numpy as np

from kubernetes import client, config
import subprocess

# To be modified
TOKEN = "kind-kind-cluster"
PROMETHEUS_URL = 'http://localhost:9090/'

# Values are in mCPU
# All other values of CPU are in mCPU
MIN_CPU_ALLOWED = 100
MAX_CPU_ALLOWED = 2000


def get_application_current_metrics(deployment_name, namespace, container_name, 
                                    cpu_request, cpu_limit, waiting_time = 45):
    deploymentObject = DeploymentObject(deployment_name, namespace, container_name,
                                        cpu_request, cpu_limit, waiting_time)
    return deploymentObject


class DeploymentObject:
    def __init__(self, deployment_name, namespace, container_name,
                 cpu_request, cpu_limit, env, waiting_time = 45):
        self.deployment_name = deployment_name
        self.namespace = namespace
        self.container_name = container_name
        self.cpu_request = cpu_request
        self.cpu_limit = cpu_limit
        
        config.load_kube_config()
        
        self.token = TOKEN
        # Seconds to wait before retrying to get metrics from prometheus after a faliure
        self.sleep = 0.3
        
        # Create a ApiClient with our config
        self.client = client.ApiClient()
        
        # v1 api
        self.v1 = client.CoreV1Api(self.client)
        
        # apps v1 api
        self.apps_v1 = client.AppsV1Api(self.client)
        
        # metrics api
        self.deployment = self.apps_v1.read_namespaced_deployment(name=self.deployment_name, namespace=self.namespace)
        
        # Update number of Pods
        self.num_pods = self.num_previous_pods = self.deployment.spec.replicas
        self.update_obs_k8s(env)
        
    # Function to obtain certain metrics of the deployment (cpu usage, cpu request, mem etc...)
    def update_obs_k8s(self, env):
        self.pod_names = []
        pods = self.v1.list_namespaced_pod(namespace=self.namespace)
        for p in pods.items:
            # if 'app.kubernetes.io/name' in p.metadata.labels and p.metadata.labels['app.kubernetes.io/name'] ==  self.deployment_name:
            if 'app.kubernetes.io/name' in p.metadata.labels and p.metadata.labels['app.kubernetes.io/name'] ==  'gem-ms-kafka-solace':
                self.pod_names.append(p.metadata.name)
        if len(self.pod_names)>1:
            print("Careful Here!!! Delicate situation; we have more than 2 pods simultaneously")   
        self.cpu_usage = 0
        self.mem_usage = 0
        
        for p in self.pod_names:
            query_cpu_usage = 'node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate'\
                '{pod="'+p+'", namespace="'+self.namespace+'", container="app"}'
                        
            query_cpu_request = 'kube_pod_container_resource_requests{namespace=' \
                        '"' + self.namespace + '", pod="' + p + '", resource="' + "cpu" + '"}'
                        
            query_cpu_limit = 'kube_pod_container_resource_limits{namespace=' \
                        '"' + self.namespace + '", pod="' + p + '", resource="' + "cpu" + '"}'

            query_mem = 'sum(irate(container_memory_working_set_bytes{namespace=' \
                        '"' + self.namespace + '", pod="' + p + '"}[5m])) by (pod)'
                             
            # -------------- CPU USAGE ----------------
            results_cpu_usage = self.fetch_metrics_using_prometheus(query_cpu_usage)
            if results_cpu_usage:
                cpu_usage = int(float(results_cpu_usage[0]['value'][1]) * 1000)  # saved as m
                self.cpu_usage += cpu_usage
                
            # -------------- CPU REQUEST ----------------
            results_cpu_request = self.fetch_metrics_using_prometheus(query_cpu_request)
            if results_cpu_request:
                cpu_request = int(float(results_cpu_request[0]['value'][1]) * 1000)  # saved as m
                if self.cpu_request != cpu_request:
                    print(f"We are changing the CPU request. Careful here!!!: OLD CPU: {self.cpu_request}, new CPU: {cpu_request}")
                    self.cpu_request = cpu_request
                    
            # -------------- CPU LIMIT ----------------
            results_cpu_limit = self.fetch_metrics_using_prometheus(query_cpu_limit)
            if results_cpu_limit:
                cpu_limit = int(float(results_cpu_limit[0]['value'][1]) * 1000)  # saved as m
                if self.cpu_limit != cpu_limit:
                    self.cpu_limit = cpu_limit
                
            # -------------- MEM ----------------
            results_mem = self.fetch_metrics_using_prometheus(query_mem)
            if results_mem:
                mem = int(float(results_mem[0]['value'][1]) / 1000000)  # saved as Mi
                self.mem_usage += mem
            
            env.update_current_state(np.array([self.cpu_usage, self.cpu_request]))
                
            
    # Function to get metrics using the prometheus pod/deployment of the cluster using the queries written above
    def fetch_metrics_using_prometheus(self, query):
        try:
            # if "container_cpu" in  query:
            #     print("\n","Prometheus query", PROMETHEUS_URL + '/api/v1/query', query, "\n",)
            # print("\n","Prometheus query", PROMETHEUS_URL + '/api/v1/query', query, "\n",)
            response = requests.get(PROMETHEUS_URL + '/api/v1/query',
                                    params={'query': query})

        except requests.exceptions.RequestException as e:
            print(e)
            print("Retrying in {}s...".format(self.sleep*10))
            time.sleep(self.sleep)
            return self.fetch_metrics_using_prometheus(query)

        if response.json()['status'] != "success":
            print("Error processing the request: " + response.json()['status'])
            print("The Error is: " + response.json()['error'])
            print("Retrying in {}s...".format(self.sleep))
            time.sleep(self.sleep)
            return self.fetch_metrics_using_prometheus(query)

        result = response.json()['data']['result']
        # print(result)
        return result
    
    # Function to update the deployment
    def update_deployment(self, new_cpu_request, new_cpu_limit=None):
        self.deployment = self.apps_v1.read_namespaced_deployment(
            name=self.deployment_name, namespace=self.namespace
        )
        
        # Modify the CPU requests and limits for the first container
        # print(f"New cpu request and converted new cpu request: {new_cpu_request, str(new_cpu_request)}")
        self.deployment.spec.template.spec.containers[0].resources.requests['cpu'] = str(new_cpu_request)+"m"
        if new_cpu_limit != None:
            self.deployment.spec.template.spec.containers[0].resources.limits['cpu'] = str(new_cpu_limit)+"m"
        
        
        try:
            # self.apps_v1.patch_namespaced_deployment(
            #     name=self.deployment_name, namespace=self.namespace, body=self.deployment
            # )
            
            # Delete and create deployment that follow are the equivalent of the patch deployment commented above
            # However, between both operations, we reset consumer group and 
            # recreate topic after deleting it. This is only useful for the application we where acting on
            self.apps_v1.delete_namespaced_deployment(name=self.deployment_name, namespace=self.namespace)
            time.sleep(5)
            reset_consumer_group()
            del_and_recreate_topics()
            
            if self.deployment.metadata.resource_version:
                self.deployment.metadata.resource_version = None
            self.apps_v1.create_namespaced_deployment(namespace=self.namespace, body=self.deployment)
            # self.apps_v1.patch_namespaced_deployment(
            #     name=self.deployment_name, namespace=self.namespace, body=self.deployment
            # )
        except Exception as e:
            print(e)
            print("Retrying in {}s...".format(self.sleep))
            time.sleep(self.sleep)
            return self.update_deployment(new_cpu_request)
        
    # Function to
    def deploy_new_pod(self, cpu_request_value_to_add: int, env):
        new_cpu_request = self.cpu_request * cpu_request_value_to_add
        new_cpu_limit = None
        new_cpu_limit = self.cpu_limit * cpu_request_value_to_add
        # if new_cpu_request >= self.cpu_limit:
        #     new_cpu_limit = self.cpu_limit + cpu_request_value_to_add
        # new_cpu_limit = self.cpu_limit + cpu_limit_value_to_add
        
        if new_cpu_request <= MAX_CPU_ALLOWED and new_cpu_request >= MIN_CPU_ALLOWED:
            self.update_deployment(new_cpu_request, new_cpu_limit)
            env.constraint_max_or_min_CPU = False
        else:
            print("Unable to Update cluster, too much cpu required to be added or removed")
            print("Nothing will be done")
            env.constraint_max_or_min_CPU = True
        pass
    

def reset_consumer_group():
    reset_consumer_group_file_path = "C:\\path\\to\\reset_consumer_group_offset.bat"
    subprocess.Popen(['cmd', '/c', 'start','/b', reset_consumer_group_file_path], shell=True)
    time.sleep(3)
    
    
def del_and_recreate_topics():
    delete_and_recreate_file_path = "C:\\path\\to\\recreate_kafka_topic.bat"
    subprocess.Popen(['cmd', '/c', 'start','/b', delete_and_recreate_file_path], shell=True)
    time.sleep(3)
    