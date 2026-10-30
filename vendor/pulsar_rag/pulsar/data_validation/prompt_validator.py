import base64
import hashlib
import json
import re
import os
import time
from hashlib import sha256
from io import StringIO
from typing import List
import pandas as pd
import asyncio
import boto3
import requests
from collections import defaultdict
from sentence_transformers import SentenceTransformer, util

from general.async_process import generic_openai
from general.loggerutils import get_logger
from pulsar.data_validation.n_diverse_context import put_diverse_context, file_transfer
from pulsar.data_store.lambda_function import retraining_data_to_s3, write_file, read_file
from pulsar.data_validation.sub_prompts.blockwise_subprompt import xml_to_subprompt
from general.pulsar.program_cache.template_cache import cache_processor
from templatize_offer import templatize_xml

logger = get_logger("GRAVTY_AI", "INFO")
ssm = boto3.client('secretsmanager')
model = SentenceTransformer('paraphrase-MiniLM-L6-v2')
authorisation = os.environ.get('authorisation')
jwt = os.environ.get('authorisation')
tenant_url = os.environ.get('tenant_url')
x_api_key = os.environ.get('x_api_key')
env = os.environ.get('ENVIRONMENT_NAME')
dynamodb_resource = boto3.resource('dynamodb')
host_url = os.environ.get('host_url')
# Role for security account
target_role_arn = os.environ.get('target_role_arn')
role_session_name = 'pulsar-retrain-data'
program_id = os.environ.get('program_id')
offer_launch_query = json.loads(os.environ.get("query").lower())
s3 = boto3.client('s3')


sts_client = boto3.client('sts')
response = sts_client.assume_role(
    RoleArn=target_role_arn,
    RoleSessionName=role_session_name
)

# Configure Boto3 with assumed role credentials
credentials = response['Credentials']
s3_client = boto3.client('s3',
                         aws_access_key_id=credentials['AccessKeyId'],
                         aws_secret_access_key=credentials['SecretAccessKey'],
                         aws_session_token=credentials['SessionToken'],
                         region_name='us-west-2'
                         )
logger.info("S3 client created successfully.")


class Prompt:
    prompt: str
    model_generated_rule: str

    def __init__(self, prompt: str):
        self.prompt = prompt
        prompt_iterate = sha256(bytes(prompt, 'utf-8')).hexdigest()
        self.model_generated_rule = read_file(f"{program_id}/gpt/rules_cache/offer/{prompt_iterate}/raw")


class Offer:
    offer_Id: str
    sponsors: List[str]
    rule: str
    templatised_rule: str
    _prompt_list: List['Prompt']
    base_prompt: Prompt
    valid_prompts: List['Prompt']

    def __init__(self, offer_id: str, sponsors: List[str], rule: str, prompts: list):
        self.offer_Id = offer_id
        self.sponsors = sponsors
        self.rule = rule
        self.templatised_rule = templatisation(xml_templatise=rule, sponsor_ids=sponsors,
                                               prompt=prompts[-1])
        self.valid_prompts = []
        if len(prompts) == 1:
            self._prompt_list = [Prompt(prompt=prompts[0])]
            self.base_prompt = Prompt(prompt=prompts[0])
            self.valid_prompts.append(self.base_prompt)
            return
        self._setBasePrompt(prompt=prompts[-1])
        self._setPrompts(prompts=prompts[:-1])
        self._prompt_validator()

    def _setBasePrompt(self, prompt: str):
        self.base_prompt = Prompt(prompt=prompt)

    def _setPrompts(self, prompts: list):
        self._prompt_list = [Prompt(prompt=prompt) for prompt in prompts]

    def _prompt_validator(self):
        for prompts in self._prompt_list:
            if calculate_similarity(prompts.prompt, self.base_prompt.prompt) > 0.75:
                self.valid_prompts.append(prompts)
        self.valid_prompts.append(self.base_prompt)


class OfferPromptValidator:
    instance = None

    def __new__(cls, **kwargs):
        if cls.instance is None:
            cls.instance = super(OfferPromptValidator, cls).__new__(cls)
            cls.instance.offers = {}
            cls.instance.offer_list = []
        return cls.instance

    @classmethod
    def process_prompts(cls, offer_id: str, sponsor_id: list):
        instance = cls.instance
        if instance is None:
            raise ValueError("Instance not created. Use the class before calling methods.")

        instance.offers[offer_id] = pd.read_csv(
            StringIO(read_file(f"{program_id}/gpt/data_store/offer/{offer_id}/data.csv")))
        df = instance.offers[offer_id]
        newest_taken = False
        for index in range(len(df) - 1, -1, -1):
            row = df.iloc[index]
            xml = row["offerXML"]
            if row["status"] == "launched" and newest_taken:  # an older saved version: not the offer any more
                df.at[index, "status"] = "Validated"
                continue
            if row["status"] == "launched" or offer_launch_query:
                newest_taken = True
                rules_prompts = row["offer_Prompt"] if pd.notna(row["offer_Prompt"]) and row[
                    "offer_Prompt"] != "" else "PLACEHOLDER_TEXT"

                df.at[index, "status"] = "Validated"
                if offer_launch_query:
                    instance.offer_list.append(Offer(offer_id=offer_id, sponsors=sponsor_id,
                                                     rule=base64.b64decode(xml).decode('utf-8'),
                                                     prompts=[rules_prompts.splitlines()[-1]]))
                    break
                else:
                    instance.offer_list.append(Offer(offer_id=offer_id, sponsors=sponsor_id,
                                                     rule=base64.b64decode(xml).decode('utf-8'),
                                                     prompts=rules_prompts.splitlines()))


def calculate_similarity(sentence1, sentence2):
    # Encode sentences
    embeddings1 = model.encode(sentence1, convert_to_tensor=True)
    embeddings2 = model.encode(sentence2, convert_to_tensor=True)

    # Calculate cosine similarity between the embeddings
    similarity_score = util.pytorch_cos_sim(embeddings1, embeddings2)[0][0].item()

    return similarity_score


def failed_blocks_filter(model_generated_xml: str, xml: str):
    if not model_generated_xml or len(model_generated_xml) == 0:
        return None
    df_model_generated_blockwise = pd.DataFrame(columns=['SubPrompt', 'Block'])
    try:
        model_generated_subprompt_block = xml_to_subprompt(model_generated_xml)

        for line, block in model_generated_subprompt_block:
            line = line.split(',,,')[1].strip()
            new_row = pd.DataFrame({'SubPrompt': [line], 'Block': [block]}, index=[0])
            df_model_generated_blockwise = pd.concat([df_model_generated_blockwise, new_row], ignore_index=True)
    except Exception as e:
        logger.info(f"Model generated XML issue : {e}")

    subprompt_block = xml_to_subprompt(xml)
    df_blockwise = pd.DataFrame(columns=['SubPrompt', 'Block'])
    if not subprompt_block:
        return None
    for line, block in subprompt_block:
        line = line.split(',,,')[1].strip()
        new_row = pd.DataFrame({'SubPrompt': [line], 'Block': [block]}, index=[0])
        df_blockwise = pd.concat([df_blockwise, new_row], ignore_index=True)
    df_combined = pd.concat([df_model_generated_blockwise[['SubPrompt']], df_blockwise[['SubPrompt']]],
                            ignore_index=True)
    duplicates = df_combined[df_combined.duplicated(keep=False)]

    # Remove duplicates from df_blockwise based on subprompts
    df_blockwise_unique = df_blockwise[~df_blockwise['SubPrompt'].isin(duplicates['SubPrompt'])]

    return df_blockwise_unique


def templatisation(xml_templatise: str, sponsor_ids: list, prompt: str):

    after_template_replace = templatize_xml(xml_templatise, prompt, program_id, sponsor_ids)
    return after_template_replace


def rag_data_fetch(xml: str, prompt: str):
    lines_list = []
    if not xml:  # not templatised: no example from this offer
        return None
    templatise_prompt_subprompt = xml_to_subprompt(xml)
    if templatise_prompt_subprompt:
        for line, block in templatise_prompt_subprompt:
            line = line.replace(',,,', ',').strip()
            lines_list.append(line)

        lines = '\n'.join(lines_list)
        prompt_subprompt_data = {'user': prompt, 'system': lines}
        return prompt_subprompt_data


def rules_retrain_data(manual_trigger):
    with open("pulsar/prompt_generation/prompt_context.txt", "r", encoding="utf-8") as file:
        prompt_writer_context = file.read()
    content = read_file(f"{program_id}/gpt/data_store/offers.csv")
    content = content.strip()
    logger.info(f"Offers: {content}")
    OfferPromptValidator()

    for lines in content.split('\n'):
        offer_id, offer_sponsor = lines.replace('"', '').split('_')
        offer_id = offer_id
        offer_sponsor = json.loads(offer_sponsor)
        OfferPromptValidator.process_prompts(offer_id=offer_id, sponsor_id=offer_sponsor)

    latest_data_list = []
    fine_tune_data = pd.DataFrame(columns=['SubPrompt', 'Block'])
    for offer in OfferPromptValidator.instance.offer_list:
        for prompt in offer.valid_prompts:
            json_rag_format = rag_data_fetch(xml=offer.templatised_rule, prompt=prompt.prompt)
            if json_rag_format:
                latest_data_list.append(json_rag_format)
        fine_tune_data = pd.concat(
            [fine_tune_data, failed_blocks_filter(model_generated_xml=offer.base_prompt.model_generated_rule,
                                                  xml=offer.rule)], ignore_index=True)
        updated_df = OfferPromptValidator.instance.offers.get(offer.offer_Id).to_csv(index=False)
        write_file(f"{program_id}/gpt/data_store/offer/{offer.offer_Id}/data.csv", updated_df)
    already_prompts_data_list = create_rag_db(latest_data_list,manual_trigger,prompt_writer_context)
    retraining_data_to_s3(file_key=f'{program_id}/gpt/data_store/offer/data.csv',
                          data=already_prompts_data_list,
                          dup_check='user')
    retraining_data_to_s3(data=fine_tune_data.to_dict(orient='records'), bucket='gravty-ai-pulsar',
                          key='blockwise_subprompt/retrain_data.csv',
                          s3_client=s3_client, dup_check='SubPrompt')
    for offer in OfferPromptValidator.instance.offer_list:  # stored now: the collected prompts can go
        s3.delete_object(Bucket=f"gravty-ai-{env}", Key=f"{program_id}/gpt/prompts/{offer.offer_Id}/offerrules_bin")


def create_rag_db(latest_data_list, manual_trigger,prompt_writer_context):
    already_prompts_data_list = []
    without_prompts_data_list = []
    if latest_data_list:
        if manual_trigger:
            s3.delete_object(
                Bucket=f"gravty-ai-{env}", Key=f'{program_id}/gpt/rag/mechanism.json'
            )

        remote_mechanism_dict = read_file(file_key=f'{program_id}/gpt/rag/mechanism.json')
        if remote_mechanism_dict:
            mechanism_dict = defaultdict(dict, json.loads(remote_mechanism_dict))
        else:
            mechanism_dict = defaultdict(dict)

        for latest_data in latest_data_list:
            if latest_data['user'] != "PLACEHOLDER_TEXT":
                already_prompts_data_list.append(latest_data)
            else:
                without_prompts_data_list.append(latest_data)

        for already_prompts_data in already_prompts_data_list:
            prompt = already_prompts_data['user']
            subprompt = already_prompts_data['system']
            subprompt_hash = generate_hash(already_prompts_data['system'])
            if len(mechanism_dict[subprompt_hash]) < 5:
                mechanism_dict[subprompt_hash][prompt] = subprompt

        for without_prompts_data in without_prompts_data_list:
            subprompt = without_prompts_data['system']
            subprompt_hash = generate_hash(without_prompts_data['system'])
            ai_generated_prompt = generic_openai(prompt_writer_context, subprompt)

            if len(mechanism_dict[subprompt_hash]) < 5:
                mechanism_dict[subprompt_hash][ai_generated_prompt] = subprompt
                already_prompts_data_list.append({"user": ai_generated_prompt, "system": subprompt})

        # Put mechanism dict
        write_file(file_key=f'{program_id}/gpt/rag/mechanism.json',data = json.dumps(dict(mechanism_dict)))

        list_of_values = mechanism_dict.values()
        rag_db_data = []
        for prompts in mechanism_dict.values():
            for user_prompt, system_prompt in prompts.items():
                rag_db_data.append({"user": user_prompt, "system": system_prompt})

        df = pd.DataFrame(rag_db_data)
        csv_buffer = StringIO()
        df.to_csv(csv_buffer, index=False)
        s3.put_object(
            Body=csv_buffer.getvalue(),
            Bucket=f"gravty-ai-{env}",
            Key=f"{program_id}/gpt/rag/offer/vectordb/data.csv"
        )
    return already_prompts_data_list

def generate_hash(example_text):
    lines = example_text.strip().split("\n")
    # every line whole, without the rule number ("Filter_1," / "rule_line_4,"): the rules' shape, not their order
    concatenated_string = "\n".join(re.sub(r"^\w+?_\d+,", "", " ".join(line.split())) for line in lines)
    hash_object = hashlib.sha256(concatenated_string.encode())
    return hash_object.hexdigest()

def call_refresh_cache_api():
    endpoint = f"{host_url}/ai/pulsar/refresh-cache"
    headers = {
        'x-api-key': x_api_key,
        "authorization": f"{jwt}",
        'content-type': 'application/json'
    }
    payload = {
        'program_id': program_id
    }
    try:
        cache_response = requests.post(endpoint, headers=headers, data=json.dumps(payload))

        if cache_response.status_code == 200:
            logger.info("Cache refreshed successfully")
        else:
            logger.info(f"Failed to refresh cache: {cache_response.status_code} - {cache_response.text}")

    except requests.exceptions.RequestException as e:
        logger.info(f"An error occurred: {e}")

async def start_cache_sync(program_id):
    try:
        rds_schema = os.environ.get('rds_schema')
        logger.info("Creating cache for the first time...")
        st=time.time()
        await cache_processor.update_program_cache_async(program_id, rds_schema=rds_schema,is_batch=True)
        end=time.time()
        logger.info(f"Cache created successfully.in time:{end-st}")
    except Exception as e:
        logger.error(f"Error during cache initialization: {str(e)}")

if __name__ == "__main__":
    if offer_launch_query:
        file_transfer(env=env, input_file=f'{program_id}/gpt/data_store/offer/data.csv')
    logger.info("Starting cache application...")
    asyncio.run(start_cache_sync(program_id))
    rules_retrain_data(offer_launch_query)
    # put_diverse_context(env=env, program_id=program_id)
    call_refresh_cache_api()
