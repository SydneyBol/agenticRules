import asyncio
import json
import time
from functools import lru_cache
from os import getenv

import jellyfish
from asyncio import get_event_loop, set_event_loop, new_event_loop, gather

from openai import OpenAI

from general.check_feature_toggle import feature_toggle_check
from general.exception_traces import getExceptionTrace
from general.pulsar.program_cache.get_program_data import Program
from general.rds_client import RdsClient
from general.loggerutils import get_logger
from general.s3_client import read_from_s3
from general.secrets_manager_client import get_secret

logger = get_logger("GRAVTY_AI")
environment_name = getenv('ENVIRONMENT_NAME')
cache_ttl = int(getenv('cache_ttl', '86400'))  # seconds; from the environment it is a string
s3_key = "attributes_endpoint.csv"
from sentence_transformers import SentenceTransformer, util
model = SentenceTransformer('paraphrase-MiniLM-L6-v2')
rds_client = RdsClient()
is_failed, OPENAI_SECRET = get_secret(f"{environment_name.upper()}_OPENAI")
if is_failed:
    raise Exception("Failed to fetch OPENAI secret")
openai_org_key = OPENAI_SECRET.get('OPENAI_ORG')
openai_api_key = OPENAI_SECRET.get('OPENAI_API_KEY')
client = OpenAI(
    api_key=openai_api_key,
    organization=openai_org_key
)
# Background task decorator



class TTLCache:
    def __init__(self, ttl=cache_ttl):  # 1 day default TTL
        self.cache = {}
        self.ttl = ttl
        self.last_refresh = {}

    def store(self, key, value):
        self.cache[key] = value
        self.last_refresh[key] = time.time()
        CacheProcessor.get_backend_code.cache_clear()  # its memoised codes came from the data just replaced

    def get(self, key):
        return self.cache.get(key, None)

    def is_expired(self, key):
        # a program never cached counts as expired (it raised KeyError, after generate_rule had built the rules)
        return key not in self.last_refresh or time.time() - self.last_refresh[key] > self.ttl



class CacheProcessor:
    def __init__(self):
        self.ttl_cache = TTLCache()
        self.programs = {}
        self.program_id_schemas = {}
        self.attribute_for_gpt = json.loads(read_from_s3(bucket=f"gravty-ai-{environment_name.lower()}", key="attribute_for_gpt.json"))

    def get_pulsar_enabled_program_ids(self, program_ids):
        enabled_program_ids = []
        for program_id in program_ids:
            pulsar_toggle = "pulsar"
            pulsar_toggle_res, pulsar_toggle = feature_toggle_check(toggle_name=pulsar_toggle, tenant_id=program_id)
            logger.info(f"pulsar toggle for program:{program_id}: {pulsar_toggle_res},{pulsar_toggle}")
            if pulsar_toggle_res is True:
                enabled_program_ids.append(program_id)
        return enabled_program_ids

    async def fetch_all_program_ids(self, environment):

        self.program_id_schemas = rds_client.get_all_program_ids()
        program_ids = self.get_pulsar_enabled_program_ids(list(self.program_id_schemas.keys()))
        logger.info(f"pulsar enabled program:{program_ids}")

        self.programs = {pid: Program(pid, self.program_id_schemas[pid]) for pid in program_ids}

    async def create_cache(self):
        logger.info("Creating cache for the first time...")
        if not self.programs:
            await self.fetch_all_program_ids(environment_name)

        # Process all programs in parallel
        tasks = [program.process() for pid, program in self.programs.items()]
        results = await gather(*tasks)

        # Store results in the cache
        for result in results:
            self.ttl_cache.store(result[0], result[1])

        logger.info(f"Cache successfully created.")


    async def update_program_cache_async(self, program_id, rds_schema=None, is_batch=None):

        logger.info(f"updating program {program_id} cache")
        # Process the program
        rds_schema = rds_schema if is_batch else self.program_id_schemas[program_id]
        program = Program(program_id, rds_schema)
        self.programs[program_id] = program
        result = await program.process(is_batch)

        # refresh that program cache
        self.ttl_cache.store(program_id, result[1])
        logger.info(f"Cache for Program {program_id} updated successfully.{self.ttl_cache.cache.get(program_id)}")

    def update_program_cache(self, program_id):
        set_event_loop(new_event_loop())
        loop = get_event_loop()  # Get the current event loop
        if loop.is_running():
            # If the event loop is already running, schedule the coroutine as a task
            asyncio.ensure_future(self.update_program_cache_async(program_id))
        else:
            # If no loop is running, run the coroutine directly
            loop.run_until_complete(self.update_program_cache_async(program_id))
        loop.close()

    def find_most_similar_levenshtein(self, word, word_list):

        word = word.lower()  # Assuming `preprocess` function exists
        word_list = [name.lower() for name in word_list]

        # Compute similarity scores using Levenshtein distance
        similarity_scores = []
        for name in word_list:
            distance = jellyfish.levenshtein_distance(word, name)
            max_length = len(word) + len(name)
            similarity_ratio = (max_length - distance) / max_length  # Normalize the distance
            similarity_scores.append(similarity_ratio)
        logger.info(f"Similarity Scores: {similarity_scores}")  # Debugging

        # Find the highest similarity ratio
        most_similar_idx = similarity_scores.index(max(similarity_scores))
        most_similar_word = word_list[most_similar_idx]
        similarity_score = similarity_scores[most_similar_idx]
        logger.info(most_similar_word)
        return most_similar_idx, similarity_score

    def most_similar_sentence_transformer(self, word, word_list):
        # Encode both the input word and the list of words
        word_embedding = model.encode(word, convert_to_tensor=True)
        word_list_embeddings = model.encode(word_list, convert_to_tensor=True)

        similarities = util.pytorch_cos_sim(word_embedding, word_list_embeddings)[0]

        most_similar_idx = similarities.argmax().item()
        most_similar_word = word_list[most_similar_idx]
        logger.info(most_similar_word)
        return most_similar_idx, similarities[most_similar_idx].item()

    def find_most_similar_gpt(self, attribute_data, value):
        logger.info(f"similarity with gpt for {attribute_data}")
        st=time.time()
        schema = '''{
        "code":""
        }'''
        context = f"""
        {attribute_data}
        based on the provided dictionary of code and names, smartly select the best relevant option based on the meaning and relation.
        You will be given a value in prompt which, your task is to return only the code in the json format {schema}  from the provided dictionary and nothing else.
        Always give a code from the dictionary and do not give empty, N/A and None code in the response.If something is not applicable give the code of the most suitable name from dictionary.
        """
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": context},
                {"role": "user",
                 "content": f"give the best suitable code for {value}"
                 }]
        )
        end = time.time()
        code = json.loads(response.choices[0].message.content).get("code")
        logger.info(f"gpt response for {value}:{code}, time:{end-st}")
        return code

    @lru_cache(maxsize=10000)
    def get_backend_code(self, program_id, attribute_level, attribute, value, dependent_id=None):
        try:
            attribute_data = self.ttl_cache.cache.get(program_id)[attribute_level].get(attribute)
            if dependent_id:
                attribute_data = attribute_data.get(dependent_id)
            if attribute_data:
                codes = list(attribute_data.keys())
                names = list(attribute_data.values())
                level_for_gpt = self.attribute_for_gpt.get(attribute_level)
                if level_for_gpt and attribute in level_for_gpt:
                    most_similar_code = self.find_most_similar_gpt(attribute_data, value)
                    return most_similar_code, 1
                else:
                    most_similar_index, similarity_score = self.most_similar_sentence_transformer(value, names)
                    logger.info(f"match code: {codes[most_similar_index]}, score:{similarity_score}")
                    if similarity_score < 0.75:
                        logger.info("Transformer similarity too low. Falling back to levenshtein similarity.")
                        levenshtein_result = self.find_most_similar_levenshtein(value, names)
                        if levenshtein_result[1] > similarity_score and levenshtein_result[1]>0.75:
                            most_similar_index, similarity_score = levenshtein_result
                    return codes[most_similar_index], similarity_score
            return value, 0
        except Exception as e:
            logger.error(f"Cache is not created for program {program_id}:{getExceptionTrace(e)}")
            return value, 0
cache_processor = CacheProcessor()
