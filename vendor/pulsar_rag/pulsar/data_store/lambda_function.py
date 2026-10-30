import csv
import datetime
import json
import logging
from io import StringIO
from os import getenv
import boto3
import pandas as pd
import requests
from pandas import json_normalize
from common_module.common_utils import get_secret, get_jwt
from common_module.redshift_data import RedshiftDataClient
from common_module.common_utils import get_parameter_value

logger = logging.getLogger()
logger.setLevel(logging.INFO)

is_batch = getenv('is_batch') is not None
logger.info(f"Batch Status: {is_batch}")

if is_batch:
    # AWS Batch environment
    region = getenv('AWS_REGION')
    boto3.setup_default_session(region_name=region)
else:
    # Lambda environment
    boto3.setup_default_session()
    redshift_cluster = getenv('REDSHIFT_CLUSTER_IDENTIFIER')
    redshift_database = getenv('REDSHIFT_DATABASE')
    secret_arn = getenv('REDSHIFT_SECRET_ARN')

    redshift = RedshiftDataClient(cluster_identifier=redshift_cluster,
                                  database=redshift_database,
                                  secret_arn=secret_arn)

ssm = boto3.client('secretsmanager')
s3 = boto3.client('s3')
env = getenv('ENVIRONMENT_NAME')
bucket_name = f"gravty-ai-{env}"
batch_job_queue = getenv('batch_job_queue')
system_manager_client = boto3.client('ssm')


tenant_type = get_parameter_value('/generic/{ENVIRONMENT_NAME}/environment'.format(ENVIRONMENT_NAME=env.lower()))


def execute_redshift_query_and_process(query):
    response = redshift.execute_query(
        sql=query
    )

    status = None
    while status != "FAILED":
        if status != 'STARTED':
            logger.info(f"Query Status: {status}")

        status = redshift.describe_statement(statement_id=response["Id"])["Status"]

        if status == "FINISHED":
            result = redshift.fetch_results(query_id=response["Id"])
            return process_redshift_result(result)

    logger.info(f"Query Status: {status}")
    raise RedshiftQueryFailed(status)


def process_redshift_result(result):
    records = result['Records']
    table_headers = result['ColumnMetadata']
    list_json_row = []

    for record in records:
        json_row = {}
        for record_row, header_row in zip(record, table_headers):
            key = header_row['name']
            if 'isNull' in record_row:
                value = ''
            else:
                value = next(iter(record_row.values()))
            json_row[key] = value
        list_json_row.append(json_row)

    return list_json_row


def data_generation_rules(date: str, program_id: str, tenant_secrets: dict, token: str, schema: str, query=None, api_trigger = None):
    offer_ids = []
    logger.info(f"program_id :{program_id}")
    logger.info(f"DateTime :{date}")
    if not query:
        if api_trigger:
            query = f"""
            SELECT *
            FROM {redshift_database}.{schema}.offers_offer
            WHERE 
                updated_ts >= Dateadd(year, -1, CURRENT_DATE)
                AND status IN( 'launched', 'suspended')
                AND offer_type = 'reward';
            """
        else:
            query = f"""
                        SELECT *
                        FROM {redshift_database}.{schema}.offers_offer
                        WHERE 
                            DATE_TRUNC('day', updated_ts) = '{date}'
                            AND status IN( 'launched', 'suspended')
                            AND offer_type = 'reward';
            """

    logger.info(f"Query : {query}")
    list_json_row = execute_redshift_query_and_process(query)

    if not list_json_row:
        return False

    logger.info(f"Data size :{len(list_json_row)}")

    for json_data in list_json_row:
        rule_id = json_data['id']
        logger.info(f"Calling rules api for : {rule_id}")
        url = f"{tenant_secrets['generic_django_endpoint']}bolapi/v1/offers/{rule_id}/drl/"
        header = {"Authorization": token, "x-api-key": tenant_secrets["x_api_key"]}
        resp = requests.get(url, headers=header)
        if 'application/json' in resp.headers['Content-Type']:
            logger.info(f"Data : {resp.json()}")
            if not resp.json():
                continue
            xml = resp.json()[0]["xml"]
            json_data["offerXML"] = xml
            json_data["sponsors"] = resp.json()[0]["sponsors"]
            # kept until the validation job has stored this offer's examples (it deletes it then): deleting here lost
            # the manager's prompts whenever that job failed
            offer_prompt = read_file(f"{program_id}/gpt/prompts/{rule_id}/offerrules_bin")
            lines = offer_prompt.split('\n')
            filtered_offer_prompt = [line for line in lines if "NoSuchKey" not in line]
            offer_prompt = '\n'.join(filtered_offer_prompt)
            if offer_prompt.startswith('\n'):
                offer_prompt = offer_prompt[1:]
            offer_ids.append(f'{rule_id}_{json_data["sponsors"]}')
            json_data["offer_Prompt"] = offer_prompt
        else:
            logger.warning("Response content is not JSON.")
            continue
        file_key = f"{program_id}/gpt/data_store/offer/{rule_id}/data.csv"
        retraining_data_to_s3(file_key=file_key, data=json_data, dup_check=["id", "updated_ts"])
    if not offer_ids or len(offer_ids) == 0:
        return False
    offers = list_to_csv_string(offer_ids)
    logger.info(f"Offer_IDs : {offers}")
    write_file(f"{program_id}/gpt/data_store/offers.csv", offers)

    return True


def lambda_handler(event, context):
    schema = event.get("schema")
    program_id = event.get("program_id")
    query = event.get("query")
    rds_schema = event.get("rds_schema")
    api_trigger = event.get("api_trigger")
    current_date = datetime.date.today()
    yesterday = current_date - datetime.timedelta(days=1)
    date = str(yesterday) + ' 00:00:00+00'
    logger.info(f"program_id :{program_id}")
    logger.info(f"DateTime :{date}")
    is_failed, tenant_secret = get_secret(f"{env.upper()}_{program_id}_SECRET")
    if is_failed:
        raise KeyError("Not Found")
    token = get_jwt(program_id)
    new_offers = data_generation_rules(date=date, program_id=program_id,
                                       tenant_secrets=tenant_secret,
                                       token=token, schema=schema, query=query,api_trigger=api_trigger)
    if new_offers:
        trigger_batch(program_id=program_id, dateMonth=yesterday.strftime('%d-%m'),
                      token=token, tenant_secret=tenant_secret, query=bool(query or api_trigger), rds_schema=rds_schema)
    return "Data successfully uploaded", 200


def retraining_data_to_s3(file_key=None, data=None, bucket=None, key=None, s3_client=None, dup_check='Prompt'):
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            logger.error("Failed to parse data string as JSON")
            return
    # Read the existing CSV content from S3
    existing_data = read_file(file_key, bucket=bucket, key=key, s3_client=s3_client)
    if existing_data != '':
        existing_data = StringIO(existing_data)
        df = pd.read_csv(existing_data)
    else:
        df = pd.DataFrame()

    if isinstance(data, list):
        if len(data) == 0:
            logger.warning("Input data list is empty.")
            return
        flattened_data = pd.concat([json_normalize(dict(item)) for item in data], ignore_index=True)
    elif isinstance(data, dict):
        flattened_data = json_normalize(data)
        if flattened_data.empty:
            logger.warning("Input data dict resulted in an empty DataFrame after normalization.")
            return
    else:
        logger.warning(f"Unexpected data type: {type(data)}. Expected list or dict.")
        return
    df = pd.concat([df, flattened_data], ignore_index=True)
    subset = [dup_check] if isinstance(dup_check, str) else list(dup_check)
    if set(subset) <= set(df.columns):  # the newest copy of a row wins (it was the oldest)
        df.drop_duplicates(subset=subset, keep='last', inplace=True)
    new_csv_data = df.to_csv(index=False, encoding='utf-8')
    try:
        write_file(file_key, new_csv_data, bucket=bucket, key=key, s3_client=s3_client)
    except Exception as e:
        raise S3WriteError(file_key) from e


def read_file(file_key: str, purge=False, bucket=None, key=None, s3_client=None):
    if s3_client:
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            if purge:
                s3_client.delete_object(Bucket=bucket, Key=key)
            return response['Body'].read().decode('utf-8')
        except s3.exceptions.NoSuchKey:
            logger.warning("no key")
            return ""
    else:
        try:
            response = s3.get_object(Bucket=bucket_name, Key=file_key)
            if purge:
                s3.delete_object(Bucket=bucket_name, Key=file_key)
            return response['Body'].read().decode('utf-8')
        except s3.exceptions.NoSuchKey:
            logger.warning("no key")
            return ""


def write_file(file_key, data, bucket=None, key=None, s3_client=None):
    if s3_client:
        s3_client.put_object(Bucket=bucket, Key=key, Body=data)
    else:
        s3.put_object(Bucket=bucket_name, Key=file_key, Body=data)


def uppercase_letters(text):
    return text.upper()


class RedshiftQueryFailed(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Redshift query failed with status: {status}")


class S3WriteError(Exception):
    def __init__(self, file_key):
        self.file_key = file_key
        super().__init__(f"S3 write error for file key: {file_key}")


def list_to_csv_string(data):
    output = StringIO()
    csv_writer = csv.writer(output, lineterminator='\n')
    for value in data:
        csv_writer.writerow([value])
    csv_string = output.getvalue()
    output.close()
    return csv_string


def trigger_batch(program_id: int, dateMonth: str, token: str, tenant_secret: dict, query: bool, rds_schema: str):
    batch_client = boto3.client('batch')
    environment_variables = {
        'program_id': program_id,
        'aws_region': getenv('AWS_REGION'),
        'AWS_REGION': getenv('AWS_REGION'),
        'is_batch': True,
        'ENVIRONMENT_NAME': env,
        'authorisation': token,
        'tenant_url': tenant_secret["generic_django_endpoint"],
        'x_api_key': tenant_secret["x_api_key"],
        'target_role_arn': getenv('target_role_arn'),
        'host_url': getenv('host_url'),
        'query': query,
        'rds_schema': rds_schema,
        'OPENAI_API_KEY':getenv("OPENAI_API_KEY"),
        'OPENAI_ORG':getenv("OPENAI_ORG")
    }
    job_definition = f'data_validation-{uppercase_letters(env)}'
    job_name = f"{env}_dataValidator_{dateMonth}"

    environment_list = [{'name': key, 'value': str(value)} for key, value in environment_variables.items()]
    response = batch_client.submit_job(
        jobName=job_name,
        jobQueue=batch_job_queue,
        jobDefinition=job_definition,
        containerOverrides={
            'environment': environment_list,
        },
        tags={
            "environment": str(env),
            "tenant_id": str(program_id),
            'team': 'ai',
            'microservice': 'pulsar',
            'name': job_name,
            'category': 'compute',
            'type' : str(tenant_type).lower()
        }
    )
    logger.info(f"Batch job submitted. Job ID: {response['jobId']}")
