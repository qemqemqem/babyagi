#!/usr/bin/env python3
import argparse
import os
import openai
import pinecone
import time
import sys
from collections import deque
from typing import Dict, List
from dotenv import load_dotenv
import os

from utils.parsing import parse_bullet_points

# Parse arguments for optional extensions
parser = argparse.ArgumentParser()
parser.add_argument('-e', '--env', nargs='+', help='filenames for env')
args = parser.parse_args()

# Load default environment variables (.env)
load_dotenv()

# Set environment variables for optional extensions
if args.env:
    for env_path in args.env:
        load_dotenv(env_path)
        print('Using env from file:', env_path)

# Set API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
assert OPENAI_API_KEY, "OPENAI_API_KEY environment variable is missing from .env"

OPENAI_API_MODEL = os.getenv("OPENAI_API_MODEL", "gpt-3.5-turbo")
assert OPENAI_API_MODEL, "OPENAI_API_MODEL environment variable is missing from .env"

if "gpt-4" in OPENAI_API_MODEL.lower():
    print(f"\033[91m\033[1m"+"\n*****USING GPT-4. POTENTIALLY EXPENSIVE. MONITOR YOUR COSTS*****"+"\033[0m\033[0m")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
assert PINECONE_API_KEY, "PINECONE_API_KEY environment variable is missing from .env"

PINECONE_ENVIRONMENT = os.getenv("PINECONE_ENVIRONMENT", "us-east1-gcp")
assert PINECONE_ENVIRONMENT, "PINECONE_ENVIRONMENT environment variable is missing from .env"

# Table config
YOUR_TABLE_NAME = os.getenv("TABLE_NAME", "")
assert YOUR_TABLE_NAME, "TABLE_NAME environment variable is missing from .env"

# Project config
OBJECTIVE = os.getenv("OBJECTIVE", "")
assert OBJECTIVE, "OBJECTIVE environment variable is missing from .env"

YOUR_FIRST_TASK = os.getenv("FIRST_TASK", "")
assert YOUR_FIRST_TASK, "FIRST_TASK environment variable is missing from .env"

#Print OBJECTIVE
print("\033[96m\033[1m"+"\n*****OBJECTIVE*****\n"+"\033[0m\033[0m")
print(OBJECTIVE)

# Configure OpenAI and Pinecone
openai.api_key = OPENAI_API_KEY
pinecone.init(api_key=PINECONE_API_KEY, environment=PINECONE_ENVIRONMENT)

# Create Pinecone index
table_name = YOUR_TABLE_NAME
dimension = 1536
metric = "cosine"
pod_type = "p1"
if table_name not in pinecone.list_indexes():
    pinecone.create_index(table_name, dimension=dimension, metric=metric, pod_type=pod_type)

# Connect to the index
index = pinecone.Index(table_name)

# Modules
MODULES = ["write text", "ask a human", "get more information", "generate an image", "refine the task into subtasks"]

# Task list
task_list = deque([])

# Central artifact (Text for now)
artifact = "nothing"

def add_task(task: Dict):
    task_list.append(task)

def get_ada_embedding(text):
    text = text.replace("\n", " ")
    return openai.Embedding.create(input=[text], model="text-embedding-ada-002")["data"][0]["embedding"]

def openai_call(prompt: str, model: str = OPENAI_API_MODEL, temperature: float = 0.5, max_tokens: int = 100):
    if not model.startswith('gpt-'):
        # Use completion API
        response = openai.Completion.create(
            engine=model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0
        )
        return response.choices[0].text.strip()
    else:
        # Use chat completion API
        messages=[{"role": "user", "content": prompt}]
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            n=1,
            stop=None,
        )
        return response.choices[0].message.content.strip()

def task_creation_agent(objective: str, result: Dict, task_description: str, task_list: List[str]):
    prompt = f"You are an task creation AI that uses the result of an execution agent to create new tasks with the following objective: {objective}, The last completed task has the result: {result}. This result was based on this task description: {task_description}. These are incomplete tasks: {', '.join(task_list)}. Based on the result, create new tasks to be completed by the AI system that do not overlap with incomplete tasks. Return the tasks as an array."
    response = openai_call(prompt, max_tokens=200)
    new_tasks = parse_bullet_points(response)
    return [{"task_name": task_name} for task_name in new_tasks]

def goal_creation_agent(objective: str):
    prompt = f"This is our objective: {objective}.\n\nI want to know when we're done. Please create a list of criteria to define our objective. We'll be done when all the criteria are satisfied.\n\nReturn the criteria as a list of bullet points."
    response = openai_call(prompt)
    return parse_bullet_points(response)

def delegation_agent(modules: str, tasks):
    tasks = "\n".join([t['task_name'] for t in tasks])
    prompt = f"You are a project planning AI agent that is tasked with breaking down problems into actionable steps and assigning those problems to one of the following AI modules: {', '.join(modules)}.\n\nFor each task, decide which module to assign it to.\n\nUse syntax like this:\n\nTask: Module\nTask: Module\n\nHere are the tasks to process:\n\n{tasks}"
    response = openai_call(prompt, max_tokens=1000)
    print("\n\nGenerated Delegation Plan:\n" + response)
    return response

def ready_refine_agent(task: str):
    prompt = f"""
You are an AI agent who's only job is to determine if a task is simple enough that an average person knows how to do it or if it would be helpful to break the task down further into easier steps.  If a task is actionable respond with only the word "READY".  If a task needs to be broken down respond with only the word "REFINE".

Example 1:
Input: Create a Website
Output: REFINE 

Example 2:
Input: Decide a domain name and use a service like GoDaddy to check availability and purchase the domain
Output: READY

Example 3:
Task: Order Takeout
Output: READY

Example 4:
Input: Pay your credit card bill
Output: READY

Example 5: 
Input: Brainstorm and outline the story, including character, setting, plot, and theme.
Output: READY

Example 6:
Input: Write a short story about a wizard who becomes a bird.
Output: REFINE

Prompt:
Input: {task}
"""
    response = openai_call(prompt)
    if "ready" in response[:10].lower():
        # No subtasks
        return False
    else:
        return True
def refinement_agent(task: str):
    if ready_refine_agent(task):
        return task
    prompt = f"""
You're a project planning AI agent that is tasked with helping people break down goals into a list
of actionable tasks that an average person knows how to do. Please break down the following task.
Task: {task}"""
    response = openai_call(prompt)
    print("\n\nMore detailed plan:\n" + response)
    return parse_bullet_points(response), True

def prioritization_agent(this_task_id: int):
    global task_list
    task_names = [t["task_name"] for t in task_list]
    next_task_id = int(this_task_id)+1
    prompt = f"""You are an task prioritization AI tasked with cleaning the formatting of and reprioritizing the following tasks: {task_names}. Consider the ultimate objective of your team:{OBJECTIVE}. Do not remove any tasks. Return the result as a numbered list, like:
    #. First task
    #. Second task
    Start the task list with number {next_task_id}."""
    response = openai_call(prompt)
    new_tasks = response.split('\n')
    task_list = deque()
    for task_string in new_tasks:
        task_parts = task_string.strip().split(".", 1)
        if len(task_parts) == 2:
            task_id = task_parts[0].strip()
            task_name = task_parts[1].strip()
            task_list.append({"task_id": task_id, "task_name": task_name})

def execution_agent(objective: str, task: str) -> str:
    context=context_agent(query=objective, n=5)
    #print("\n*******RELEVANT CONTEXT******\n")
    #print(context)
    prompt =f"You are an AI who performs one task based on the following objective: {objective}.\nTake into account these previously completed tasks: {context}\nYour task: {task}\nResponse:"
    return openai_call(prompt, temperature=0.7, max_tokens=2000)

def context_agent(query: str, n: int):
    query_embedding = get_ada_embedding(query)
    results = index.query(query_embedding, top_k=n, include_metadata=True)
    #print("***** RESULTS *****")
    #print(results)
    sorted_results = sorted(results.matches, key=lambda x: x.score, reverse=True)
    return [(str(item.metadata['task'])) for item in sorted_results]

def decide_if_done_agent(objective: str, artifact: str):
    goals = '\n'.join(goal_list)
    prompt = f"We're trying to complete this objective: {objective}.\n\nHere are the criteria for success:{goals}\n\nThis is what we've written so far: {artifact}.\n\nDo you think the objective is complete? If yes, please give a single word answer of 'yes'. If no, please list the criteria which have not yet been achieved"
    response = openai_call(prompt, max_tokens=10)
    if response[:3].lower() == "yes":
        print("\033[94m" + "Yes this is great!" + "\033[0m")
        return True
    else:
        print("\033[94m" + f"Not yet done, still need to achieve these goals: {response}" + "\033[0m")
        return False

def modify_artifact_from_task_agent(objective: str, artifact: str, task: str, result: str):
    prompt = f"{artifact}\n\nThat's what we've written so far.\n\nWe're trying to complete this objective: {objective}.\n\nWe've decided to do this task: {task}.\n\nThis is the result of that: {result}.\n\nDo you think we should rewrite what we've written so far based on the result of that task? If no, please give a single word answer of 'no'. If yes, please give a single word answer of 'yes' and then rewrite what we've written so far to incorporate the result of that task."
    response = openai_call(prompt, max_tokens=2000)
    if response[:2].lower() == "no":
        print(f"Not yet done, still need to achieve these goals: {', '.join(goal_list)}")
        return artifact
    if response[:3].lower() == "yes":
        response = response[3:].strip()
        if response[0] == ".":
            response = response[1:].strip()
        if response[:10].lower() == "rewritten:":
            response = response[10:].strip()
        return response
    # By default, no changes
    print("Confusing response: " + response)
    return artifact

# Add the first task
first_task = {
    "task_id": 1,
    "task_name": YOUR_FIRST_TASK
}

# # Goal list. This is separate from the tasks, because these will help us decide when we're done.
# goal_list = goal_creation_agent(OBJECTIVE)
# print("\033[94m\033[1m"+"\n*****GOALS*****\n"+"\033[0m\033[0m")
# for goal in goal_list:
#     print(goal)
goal_list = []

add_task(first_task)
# Main loop
task_id_counter = 1
while True:
    if task_list:
        # Print the task list
        print("\033[95m\033[1m"+"\n*****TASK LIST*****\n"+"\033[0m\033[0m")
        for t in task_list:
            print(str(t['task_id'])+": "+t['task_name'])

        # Step 1: Pull the first task
        task = task_list.popleft()
        print("\033[92m\033[1m"+"\n*****NEXT TASK*****\n"+"\033[0m\033[0m")
        print(str(task['task_id'])+": "+task['task_name'])

        # # Refine the task if needed
        # task, subtasks = refinement_agent(task['task_name'])
        refinement_agent(task['task_name'])

        # Send to execution function to complete the task based on the context
        result = execution_agent(OBJECTIVE,task["task_name"])
        this_task_id = int(task["task_id"])
        print("\033[93m\033[1m"+"\n*****TASK RESULT*****\n"+"\033[0m\033[0m")
        print(result)

        # Modify the artifact based on the result of the task
        artifact = modify_artifact_from_task_agent(OBJECTIVE, artifact, task["task_name"], result)
        print("\033[94m\033[1m"+"\n*****ARTIFACT*****\n"+"\033[0m\033[0m")
        print(artifact)

        # Step 2: Enrich result and store in Pinecone
        enriched_result = {'data': result}  # This is where you should enrich the result if needed
        result_id = f"result_{task['task_id']}"
        vector = enriched_result['data']  # extract the actual result from the dictionary
        index.upsert([(result_id, get_ada_embedding(vector),{"task":task['task_name'],"result":result})])

    # Step 3: Create new tasks and reprioritize task list
    new_tasks = task_creation_agent(OBJECTIVE,enriched_result, task["task_name"], [t["task_name"] for t in task_list])

    # # Create a delegation plan
    # delegation_plan = delegation_agent(MODULES, new_tasks)

    for new_task in new_tasks:
        task_id_counter += 1
        new_task.update({"task_id": task_id_counter})
        add_task(new_task)
    prioritization_agent(this_task_id)

    # # Quit if done
    # if decide_if_done_agent(OBJECTIVE, artifact):
    #     print("\033[94m\033[1m"+"\n*****DONE*****\n"+"\033[0m\033[0m")
    #     break

    time.sleep(1)  # Sleep before checking the task list again

# Print the final artifact
print("\033[94m\033[1m"+"\n*****FINAL ARTIFACT*****\n"+"\033[0m\033[0m")
print(artifact)