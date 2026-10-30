import base64
import json
import os
import re
from concurrent.futures.thread import ThreadPoolExecutor
from threading import Thread
from hashlib import sha256
from os import getenv
from uuid import uuid4
import requests
from cachetools import Cache
from flask import Blueprint, request, Response
from openai import OpenAI
from general import constants
from general.check_feature_toggle import feature_toggle_check
from general.dynamodb_resource import DbHelper
from general.errors import Error, AIError
from general.exception_traces import getExceptionTrace
from general.loggerutils import get_logger
from general.pulsar.blockutils import get_all_subprompts, get_all_blocks, process_xml, blocks_to_offer, get_all_tags
from general.responses_to_ui_trustconnect import get_error_message
from general.s3_resource import put_object_into_s3, get_object_from_s3
from general.secrets_manager_client import get_secret
from general.vectorDB.contextDB import get_db_by_program, rag_cache
from general.validate_variable import validate
from general.utilities import jwt_utils
from general.pulsar.program_cache.template_cache import cache_processor

TIME_FORMAT = "%Y-%m-%d-%H-%M-%S-%f"
gpt_offer_prompt = Blueprint('gpt_offer_prompt', __name__)

logger = get_logger("GRAVTY_AI")
ENVIRONMENT_NAME = getenv('ENVIRONMENT_NAME')
ddb_utils = DbHelper()

is_failed, OPENAI_SECRET = get_secret(f"{ENVIRONMENT_NAME.upper()}_OPENAI")
if is_failed:
    raise Exception("Failed to fetch OPENAI secret")

OPENAI_SYSTEM_MESSAGE = os.environ.get('OPENAI_SYSTEM_MESSAGE', "You are a helpful assistant.")

openai_org_key = OPENAI_SECRET.get('OPENAI_ORG')
openai_api_key = OPENAI_SECRET.get('OPENAI_API_KEY')
client = OpenAI(
    api_key=openai_api_key,
    organization=openai_org_key
)

offer_cache = Cache(maxsize=99999)
secret_cache = Cache(maxsize=99999)


def get_from_cache(prompt_hash, program_id, regenerate, rule_id):
    cached_response = offer_cache.get(prompt_hash.hexdigest(), None)
    if cached_response is not None and not regenerate:
        logger.info("found cached response in local cache")
        return cached_response
    elif not regenerate:
        s3_cached_raw = get_object_from_s3(key=f"{program_id}/gpt/rules_cache/offer/{prompt_hash.hexdigest()}/raw")
        s3_cached_xml = get_object_from_s3(key=f"{program_id}/gpt/rules_cache/offer/{prompt_hash.hexdigest()}/xml")

        if s3_cached_raw not in [None, "NoSuchKey"] and s3_cached_xml not in [None, "NoSuchKey"]:
            logger.info(f"found cached response in s3, raw: {s3_cached_raw}")
            response = json.dumps({"xml": s3_cached_xml, "raw": s3_cached_raw, "rule_id": rule_id})
            return response
    return None


def store_prompt(program_id, rule_id, prompt):
    obj = get_object_from_s3(key=f"{program_id}/gpt/prompts/{rule_id}/offerrules_bin")
    if obj == 'NoSuchKey' or obj == '' or obj is None:
        obj = prompt
    else:
        obj = obj + '\n' + prompt
    put_object_into_s3(key=f"{program_id}/gpt/prompts/{rule_id}/offerrules_bin",
                       object_data=bytes(obj, "utf-8"))
def generate_uuid():
    return uuid4()


@gpt_offer_prompt.route("/ai/pulsar/offer/rules", methods=['POST'])
def generate_rule():
    try:
        logger.info("getting tenant details from rds")
        tenant_dict = jwt_utils.decode_jwt(request.headers.get('authorization'))
        program_id = str(tenant_dict["program_id"])
        access_level = tenant_dict["access_level"]
    except KeyError as e:
        logger.error(f"failed to get tenant details - {getExceptionTrace(e)}")
        error_code = "FAILED_TO_FETCH_TENANT_DETAILS"
        return get_error_message(error_code=error_code), 404
    if access_level != 'Program':
        logger.error("User is not authorized to perform this action")
        return Error.UnauthorisedUser.value.error, 403
    pulsar_toggle = "pulsar"
    pulsar_toggle_res, pulsar_toggle = feature_toggle_check(toggle_name=pulsar_toggle, tenant_id=program_id)
    if pulsar_toggle_res is False:
        return Error.PulsarNotEnabled.value.error, 404
    regenerate = json.loads(request.args.get('regenerate', 'true'))
    logger.info(f"Regenerate param value : {regenerate}")
    try:
        logger.info("rules completion request received")
        request_body = request.json
        object_id = request_body.get('id')
        if object_id and pulsar_toggle["configuration"]["offer"] is False:
            return Error.OfferNotEnabled.value.error, 404
        prompt = validate_prompt(request_body.get('prompt', "").strip(), os.getenv("OFFER_PROMPT_MAX_LENGTH", 5000))
        rule_id = str(request_body.get('id', f"offer_{generate_uuid()}")).strip()

        sponsor_id = request_body.get('sponsor_id')

        # Validate Sponsor ID
        sponsor_table_name = f"{ENVIRONMENT_NAME.upper()}_{program_id}_Sponsor"
        sponsor = ddb_utils.get_item_from_dynamodbtable(tablename=sponsor_table_name,
                                                        key={"SponsorID": int(sponsor_id)})
        if sponsor is None:
            logger.warn("invalid sponsor id")
            raise Error.InvalidSponsorId.value

        # put prompt in s3
        store_prompt(program_id, rule_id, prompt)

        prompt_tokens = prompt.split()
        if len(prompt_tokens) < 4:
            logger.info("insufficient tokes to generate the response")
            return Error.InsufficientTokens.value.error

        prompt_hash = sha256(bytes(prompt, 'utf-8'))
        # get from cache
        response = get_from_cache(prompt_hash, program_id, regenerate, rule_id)
        if response:
            return Response(response, mimetype="application/json"), 200

        logger.info("generating rule for Offer")

        subprompt_post_fill, subprompt_pre_fill = get_all_subprompts(prompt, request.headers.get('authorization'),
                                                                     int(sponsor_id), int(program_id)
                                                                     )

        with ThreadPoolExecutor() as executor:
            blocks = executor.submit(get_all_blocks, subprompt_post_fill, int(program_id), int(sponsor_id))
            tags = executor.submit(get_all_tags, subprompt_pre_fill)
            blocks_for_each_subprompt = blocks.result()
            rule_tags = tags.result()

        final_xml = process_xml(blocks_to_offer(blocks_for_each_subprompt, rule_tags))
        b64_final_xml = base64.b64encode(bytes(final_xml, 'utf-8'))

        logger.info(f"final_xml: \n {final_xml} \n\n base64encode: \n {b64_final_xml}")
        response = json.dumps({"xml": str(b64_final_xml)[2:-1], "raw": str(final_xml), "rule_id": rule_id})

        offer_cache[prompt_hash.hexdigest()] = response

        put_object_into_s3(f"{constants.ai_bucket}-{ENVIRONMENT_NAME}",
                           f"{program_id}/gpt/rules_cache/offer/{prompt_hash.hexdigest()}/raw",
                           bytes(final_xml, "utf-8"))
        put_object_into_s3(f"{constants.ai_bucket}-{ENVIRONMENT_NAME}",
                           f"{program_id}/gpt/rules_cache/offer/{prompt_hash.hexdigest()}/xml", b64_final_xml)
        if cache_processor.ttl_cache.is_expired(int(program_id)):
            Thread(target=cache_processor.update_program_cache, args=(int(program_id),)).start()

        return Response(response, mimetype="application/json"), 200
    except KeyError as e:
        logger.error(f"{getExceptionTrace(e)}")
        return Error.MissingPrompt.value.error
    except AIError as e:
        logger.error(f"{getExceptionTrace(e)}")
        return e.error
    except Exception as e:
        logger.error(f"{getExceptionTrace(e)}")
        return Error.Unknown.value.error


def validate_prompt(prompt: str, max_length: int = 5000):

    # Validating length of prompt Denial of Service (DoS) attacks
    if len(prompt) > max_length:
        logger.warning(f"Prompt exceeds maximum allowed length of {max_length} characters.")
        raise Error.OfferPromptTooLarge.value

    # Validating JSON Injections
    if re.search(r"(\{.*?\})", prompt):
        logger.warning("JSON Injections detected in the prompt")
        raise Error.OfferPromptNotAcceptable.value

    # Validating SQL Injections
    sql_keywords = r"\b(SELECT|INSERT|DELETE|UPDATE|DROP|EXEC|ALTER|CREATE|GRANT|UNION)\b"
    if re.search(sql_keywords, prompt, re.IGNORECASE):
        # Check for usage context (e.g., "update the offer details" is fine)
        suspicious_patterns = r"(;\s*|--|\bFROM\b|\bWHERE\b|\bTABLE\b)"
        if re.search(suspicious_patterns, prompt, re.IGNORECASE):
            logger.warning("SQL Injections detected in the prompt")
            raise Error.OfferPromptNotAcceptable.value

    # Validating Command Injections, Cross site scripting (XSS) or SQL Injections
    dangerous_chars = os.getenv("PROMPT_INVALID_CHARACTERS_REGEX", r"[;<>{}[\]\\*~^@]")
    if re.search(dangerous_chars, prompt):
        logger.warning("Input contains potentially malicious code.")
        raise Error.UnsupportedCharacters.value

    # Sanitizing input prompt
    sanitized_input = prompt.replace("<", " lesser than ").replace(">", " greater than ").replace("&", " and ")
    return sanitized_input


@gpt_offer_prompt.route("/ai/pulsar/refresh-cache", methods=['POST'])
def refresh_cache():
    # the program is the caller's own (its JWT), so one tenant cannot rebuild another's examples
    try:
        program_id = str(jwt_utils.decode_jwt(request.headers.get('authorization'))["program_id"])
    except KeyError as e:
        logger.error(f"failed to get tenant details - {getExceptionTrace(e)}")
        return get_error_message(error_code="FAILED_TO_FETCH_TENANT_DETAILS"), 404
    asked = (request.get_json(silent=True) or {}).get('program_id')
    if asked is not None and str(asked) != program_id:
        return Error.UnauthorisedUser.value.error, 403
    logger.info(f"RAG cache value for program_id {program_id} before refresh: {rag_cache.get(int(program_id))}")

    rag_cache[int(program_id)] = None
    logger.info(f"RAG cache value for program_id {program_id} after refresh: {rag_cache.get(int(program_id))}")
    get_db_by_program(int(program_id))

    return json.dumps({"message": "Cache refreshed successfully"}), 200