from gymnasium import Env, spaces

import numpy as np
import time

from rl_utils import get_application_current_metrics

#
# global constants
#

# Values are in mCPU
# All other values of CPU are in mCPU
MIN_CPU_ALLOWED = 10
MAX_CPU_ALLOWED = 4000

# Will be very userul when one episode will have more than one step. 
# This is not the case for the moment
MAX_STEPS = 50  # MAX Number of steps per episode

# action values
# Action space is always defined as integers in GYMNASIUM Environment
ACTION_DO_NOTHING = 0
ACTION_INCREASE_MILLI_CPU_BY_25_PERCENT = 1
ACTION_INCREASE_MILLI_CPU_BY_50_PERCENT = 2
ACTION_INCREASE_MILLI_CPU_BY_75_PERCENT = 3
ACTION_DECREASE_MILLI_CPU_BY_20_PERCENT = 4
ACTION_DECREASE_MILLI_CPU_BY_33_PERCENT = 5
ACTION_DECREASE_MILLI_CPU_BY_43_PERCENT = 6
ACTION_ARRAY = ["Do-Nothing", "Add-miliCPU-by-25-percent", "Add-miliCPU-by-5-percent", "Add-miliCPU-by-75-percent",
                "Remove-miliCPU-by-20-percent", "Remove-miliCPU-by-33-percent", "Remove-miliCPU-by-43-percent",]


# Useful to access/target the microservice/application we are working on
deployment_name = 'gem-bridges-gem-ms-kafka-solace'
namespace = 'skube-localdev' 
container_name = "kafka"
# These values are default values and will not influence the rest.
# Actual values of of the microservice will be scraped using prometheus.
cpu_request = 200
cpu_limit = 500


class BasicEnv(Env):
    # Environment initialization
    # Waiting time is in seconds
    def __init__(self, env_config=None, waiting_period = 45):
        
        self.cumulative_reward = 0
        self.reward_alpha = 1.0 # Coefficient for under provisioning penalty in R2 fxn
        self.reward_beta = 1000 # Coefficient for over provisioning penalty in R2 fxn
        
        # Define the self.state variable. The values here are not important since they will be overwritten in the reset and step functions
        self.state = np.array([np.float64(0), np.float64(0)])
        
        # Definition of the action space i.e. Accepted actions
        self.action_space = spaces.Discrete(n=len(ACTION_ARRAY), start=0)
        
        self.number_of_actions = len(ACTION_ARRAY)
        self.min_cpu_allowed = MIN_CPU_ALLOWED
        self.max_cpu_allowed = MAX_CPU_ALLOWED
        self.episode_over = False
        self.average_cpu = []
        self.current_step = 0
        self.constraint_max_or_min_CPU = False
        self.waiting_period = waiting_period
        self.deployment_object = get_application_current_metrics(deployment_name, namespace, container_name, cpu_request, cpu_limit, self)
                
        # Definition of state space i.e. Valid state space. Each state has 2 values: actual cpu and allocated cpu (request value) at a given moment
        # We defined each state as an array of 2 valaues (shape), and defined the min and max possible values for each element.
        # Consult the gymanisum documentation for more details and a better understanding of the box space (https://gymnasium.farama.org/api/spaces/fundamental/#box)
        self.observation_space = self.get_observation_space()
        # self.reset(seed=np.random.seed())
        
        
    def step(self, action):
        info = {} 
        terminated = False
        truncated = False
        reward = 0.0
        new_state = np.array([0,0])
        # new_state = np.array([0])
        previous_state = self.state
        
        self.take_action(action)
        
        # Wait a few seconds if on real k8s cluster
        if action != ACTION_DO_NOTHING and self.constraint_max_or_min_CPU is False:
            # logging.info('[Step {}] | Waiting {} seconds for enabling action ...'
            # .format(self.current_step, self.waiting_period))
            time.sleep(self.waiting_period)  # Wait a few seconds...
        
        self.deployment_object.update_obs_k8s(self)
        while self.deployment_object.cpu_usage == 0:
            print("Pod is not ready")
            time.sleep(5)
            self.deployment_object.update_obs_k8s(self)
            pass
        reward = self.calculate_reward(self.deployment_object.cpu_usage, self.deployment_object.cpu_request, self.reward_alpha, self.reward_beta)
        self.cumulative_reward+=reward
        print(self.current_step, ACTION_ARRAY[action], previous_state, self.state, reward, self.cumulative_reward)
        new_state = self.state
        # if self.episode_over:
        #     terminated = True
        terminated = True
        # info["cumulative_reward"]=self.cumulative_reward
        info.update({"cumulative_reward": self.cumulative_reward})
        
        if new_state not in self.observation_space:
            print(new_state)
            truncated  = True
            terminated = False
            raise Exception(f'Invalid new state:{new_state} from previous state: {previous_state} taking action:{ACTION_ARRAY[action]}')
        
        # self.reset()
        # Return new_state, reward, terminated and any additional info we want to transmit. 
        # To get the difference between terminated and truncated, take a loot at the documentation (https://gymnasium.farama.org/api/env/#gymnasium.Env.step)
        return new_state, reward, terminated, truncated, info
    
     
    def reset(self, *, seed =None, options=None):
        if seed is not None:
            super().reset(seed=seed)
        info = {}
        self.cumulative_reward = 0
        self.episode_over = False
        self.average_cpu = []
        self.current_step = 0
        
        self.deployment_object = get_application_current_metrics(deployment_name, namespace, container_name, cpu_request, cpu_limit, self)
        
        initial_state = self.get_current_state()
        self.state = initial_state
        print(f"\ninitial state:{initial_state}")
        
        if initial_state not in self.observation_space:
            print(initial_state)
            raise Exception(f'Invalid new state: {initial_state}')
        
        # Return the initial state and any additional info.
        # Having a random initial state is good for exploration
        return initial_state, info
    
    
    def render(self):
        # visualization to be added here
        pass
    
    # Function to define the observation space
    # Observation space (states) consists fo 2 values: current cpu used and cpu request
    def get_observation_space(self):
        return spaces.Box(
                low=np.array([0, self.min_cpu_allowed,]), 
                high=np.array([self.max_cpu_allowed,self.max_cpu_allowed,]),
                shape=(2,),
                dtype=np.float64
            )
        
        return spaces.Box(
                low=np.array([-3990,]), 
                high=np.array([3990,]),
                shape=(1,),
                dtype=np.float64
            )
        
    # Function to get the current state from the current metrics of the app on the cluster
    def get_current_state(self):
        return np.array([self.deployment_object.cpu_usage, self.deployment_object.cpu_request])
        # return np.array([self.deployment_object.cpu_request-self.deployment_object.cpu_usage])
    
    # Function to decide which action to take and execute one time step within the environment
    def take_action(self, action):
        self.current_step +=1
        
        if self.current_step == MAX_STEPS:
            self.episode_over = True
        
        # ACTIONS
        if action == ACTION_DO_NOTHING:
            pass
        
        elif action == ACTION_INCREASE_MILLI_CPU_BY_25_PERCENT:
            self.deployment_object.deploy_new_pod(1.25, self)
        
        elif action == ACTION_INCREASE_MILLI_CPU_BY_50_PERCENT:
            self.deployment_object.deploy_new_pod(1.5, self)
        
        elif action == ACTION_INCREASE_MILLI_CPU_BY_75_PERCENT:
            self.deployment_object.deploy_new_pod(1.75, self)
        
        elif action == ACTION_DECREASE_MILLI_CPU_BY_20_PERCENT:
            self.deployment_object.deploy_new_pod(0.8, self)
        
        elif action == ACTION_DECREASE_MILLI_CPU_BY_33_PERCENT:
            self.deployment_object.deploy_new_pod(0.67, self)
        
        elif action == ACTION_DECREASE_MILLI_CPU_BY_43_PERCENT:
            self.deployment_object.deploy_new_pod(1/1.75, self)
            pass
        
        else:
            raise Exception('Invalid Action: ', action)
        
        pass
    
    # Function to calculate reward
    def calculate_reward(self, cpu_actual:int, cpu_allocated:int, alpha:float = 1.0, beta:float = 1.0):
        if self.constraint_max_or_min_CPU == True:
            return -alpha * cpu_allocated
        
        under_provisioning = max(0, cpu_actual*1.2 - cpu_allocated)
        over_provisioning = max(0, cpu_allocated - cpu_actual*1.2)
        
        if over_provisioning == 0 and under_provisioning > 0: # If there is no over_provisioning
            reward = -alpha*under_provisioning
        elif under_provisioning == 0 and over_provisioning > 0:
            reward = beta * 1/over_provisioning
        else: # There was an error somewhere and no condition was fullfilled
            reward = 0
            print("Very strange situation, recheck rewards")
        return reward
        return -alpha * MAX_CPU_ALLOWED - beta * MAX_CPU_ALLOWED
    
    # Small function to update current environment state
    def update_current_state(self, new_state):
        self.state = new_state
    