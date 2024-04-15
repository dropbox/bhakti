#!/opt/tensorflow/bin/python3

import boto3
from botocore.exceptions import ClientError
import json
from pathlib import Path
import logging
import requests
from tensorflow.python.keras.protobuf.saved_metadata_pb2 import SavedMetadata
import subprocess
from datetime import datetime
import os

SQS_QUEUE = os.getenv('SQS_QUEUE')
AWS_REGION = os.getenv('AWS_REG')
HUGGINGFACE_TOKEN = os.getenv('HUGGINGFACE_TOKEN')
MODEL_DIRECTORY='/tmp/models'
DYNAMO_STATUS_TABLE  = os.getenv('DYNAMO_STATUS_TABLE')
LOGGING_BUCKET = os.getenv('LOGGING_BUCKET')

logger = logging.getLogger()
logging.basicConfig(filename='/var/log/bhakti.log', encoding='utf-8', level=logging.DEBUG)
logger.setLevel(logging.INFO)

def get_api_token(): 
    client = boto3.client('secretsmanager', region_name=AWS_REGION)  
    get_secret_value_response = client.get_secret_value(SecretId=HUGGINGFACE_TOKEN)
    secret = get_secret_value_response['SecretString']
    return secret

def download_metadata_file(msg_body, token): 
    model = msg_body['id']
    filename = ''
    for file in msg_body['siblings']:
        if 'keras_metadata.pb' in file['rfilename']:
            filename = file['rfilename']
    logger.info((f'Attempting to download {model}/{filename} from HuggingFace'))
    
    downloadLoc = Path(f"{MODEL_DIRECTORY}/{model}/{filename}")
    downloadLoc.parent.mkdir(parents=True, exist_ok=True)    
    downloadLink = f"https://huggingface.co/{model}/resolve/main/{filename}"
    logger.info((f'TRYING: {downloadLink}'))

    headers = {
    'Authorization': f'Bearer {token}'
    }

    try: 
        response = requests.get(downloadLink, headers=headers)
        if response.status_code == 401:
            downloadLoc = describe_no_access(downloadLoc)
            logger.info((f"Code 401: {downloadLoc}"))
        elif response.status_code == 200: 
            with open(downloadLoc, "wb") as resultFile:
                resultFile.write(response.content)
                logger.info((f'wrote file to {downloadLoc}'))

    except Exception as e:
        with open(f'{downloadLoc}-FAILED', 'w') as failed:
            failed.write("COULD NOT DOWNLOAD")
            logger.error((e))
            return f'{downloadLoc}-FAILED'

    return downloadLoc

def describe_no_access(location):
    with open(f'{location}-GATED', 'w') as noAccess:
        noAccess.write("CAN'T FETCH")
        logger.info(("couldn't access model"))
    return f'{location}-GATED'

def check_for_code(local_file):
    metadata = {}
    saved_metadata = SavedMetadata()
    logger.info((f"******* Checking {local_file} for keras Lambda Layer *********"))
    try:
        with open(local_file, 'rb') as f:
            saved_metadata.ParseFromString(f.read())
        lambda_code = [layer["config"]["function"]["items"][0]
            for layer in [json.loads(node.metadata)
                for node in saved_metadata.nodes
                if node.identifier == "_tf_keras_layer"]
            if layer["class_name"] == "Lambda"]
        for code in lambda_code:
            logger.info((f"found code in {local_file}: {code}"))
            logger.info((f"CODE: {code}"))
        code = lambda_code[0]
        metadata['extracted_encoded_code'] = code
        metadata['contains_code'] = True
        return metadata
    # If we don't find a lambda layer, the above check will give an IndexError that we can assume
    # that the model does not contain a Lambda layer
    except IndexError as ie:
        metadata['contains_code'] = False
        logger.info(("didn't find code"))
        return metadata
    except Exception as e:
        logger.info((f'We had a non-index error analyzing {local_file} : {e}'))
        return metadata

def update_dynamo(result):
    result['model_type'] = 'protobuf' 
    model = result["repo"]

    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMO_STATUS_TABLE)
    
    response = table.query(
        Select='COUNT',
        KeyConditionExpression='repo = :repo',
        ExpressionAttributeValues={':repo': model}
    )

    if response['Count'] > 0:
        #get and preserve prior analysis
        old_analysis = table.get_item(Key={'repo': model, 'version': 'v0'})
        version = f"v{response['Count']}"
        old_analysis['Item']['version'] = version
        table.put_item(
            Item = old_analysis['Item']
        )

        #update table with new data
        result['version'] = 'v0'
        addition_response = table.delete_item(Key={'repo': model, 'version': 'v0'})
        table.put_item(
            Item = result
        )
        logger.info((f'Added {result["repo"]} got code {addition_response["ResponseMetadata"]["HTTPStatusCode"]}'))

    else:
        logger.info((f'New model {result["repo"]} analyzed, adding to metadata store'))    
        result['version'] = 'v0'
        addition_response = table.put_item(
            Item = result
        )
        logger.info((f'Added {result["repo"]} got code {addition_response["ResponseMetadata"]["HTTPStatusCode"]}'))

sqs = boto3.resource('sqs', region_name=AWS_REGION)
bhakti_queue = sqs.get_queue_by_name(
    QueueName=SQS_QUEUE
)
api_token = get_api_token()

scanning = True
while scanning: 
    sqs_messages = bhakti_queue.receive_messages(
            MaxNumberOfMessages=1,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
            WaitTimeSeconds=20,
        )
    if len(sqs_messages) == 0:
        scanning = False
    for sqs_message in sqs_messages:
        body = sqs_message.body
        msg_body = json.loads(body)
        logger.info((f'SQS GIVING US {msg_body}'))
        model = msg_body['id']

        local_file = download_metadata_file(msg_body, api_token)
        logger.info((local_file))
        sqs_message.delete()
        
        result = {}
        if str(local_file).endswith('-GATED'):
            logger.info((f'{model} is not publicly available'))
            result['private'] = True
        else:       
            result = check_for_code(local_file)
        result['repo'] = model
        result['modified_date'] = msg_body['lastModified']
        result['keras_filenam'] = msg_body['keras_filename']
            
        logger.info((f'RESULTS {result}'))
        update_dynamo(result)

try:
    s3 = boto3.client('s3', region_name=AWS_REGION)
    s3.put_object(Bucket = LOGGING_BUCKET, Key=f"{int(round(datetime.timestamp(datetime.now())))}-bhakti.log", Body='/var/log/bhakti.log')
except Exception as e:
    print('unable to upload log to s3')

subprocess.call(["shutdown"])