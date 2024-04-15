import json
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
import logging
import requests
import re
import hashlib
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)


DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
EC2_AMI = 'ami-0b28c78d9f575dfa1'
DYNAMO_TABLE = os.getenv('DYNAMO_TABLE')
WORKING_QUEUE = os.getenv('WORKING_QUEUE')
HF_TOKEN = os.getenv('HF_TOKEN')
AWS_REGION = os.getenv('AWS_REG')
INSTANCE_PROFILE_ARN = os.getenv('INSTANCE_PROFILE_ARN')
LOGGING_BUCKET = os.getenv('LOGGING_BUCKET')
ANALYSIS_BUCKET = os.getenv('ANALYSIS_BUCKET')
ANALYSIS_PATH = os.getenv('ANALYSIS_PATH')

def get_user_data(bucket: str) -> str:
    user_data = f"""#!/bin/bash
aws s3 cp s3://{bucket} /tmp/analysis/scripts.zip
unzip /tmp/analysis/scripts.zip -d /tmp/analysis
chmod +x /tmp/analysis/monitoring_ec2_check.py
export SQS_QUEUE={WORKING_QUEUE}
export AWS_REG={AWS_REGION}
export HUGGINGFACE_TOKEN={HF_TOKEN}
export DYNAMO_STATUS_TABLE={DYNAMO_TABLE}
export LOGGING_BUCKET={LOGGING_BUCKET}
./tmp/analysis/monitoring_ec2_check.py"""
    return user_data

def get_api_token():
    secret_name = HF_TOKEN 
    region_name = AWS_REGION
    
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager', 
        region_name=region_name
    )
    
    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )

    except ClientError as e:
        print(e)
        raise e

    secret = get_secret_value_response['SecretString']
    return secret

def callHuggingFace(urlpointer, token):
    url = urlpointer
    payload = {}
    headers = {
    'Authorization': f'Bearer {token}'
    }

    response = requests.request("GET", url, headers=headers, data=payload)
    return response

def findKeras(models, modelType): 
    with open(f'/tmp/kerasFriends-{modelType}.txt', 'a' ) as kerasFriends:
        for model in models:
            for file in model['siblings']:
                if modelType in file['rfilename']:
                    model['keras_filename'] = file['rfilename']
                    kerasFriends.write(json.dumps(model))
                    kerasFriends.write("\n")

def scanPublicModels(url, api_token, modelType):    
    response = callHuggingFace(url, api_token)
    models = response.json()
    findKeras(models, modelType)

    try:
        nextPage = response.headers['link']
        url = re.search('<(.+?)>', nextPage).group(1)
        return url

    except Exception as sslE:
        url = 'DONE'
        return url


def check_if_model_updated(id, lastModified):
    latest_modification_date = datetime.strptime(lastModified, DATE_FORMAT)
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMO_TABLE)
        response = table.get_item(
            Key={'repo': id, 'version': 'v0'} 
        )

        if 'Item' not in response.keys():
            logger.info(f'New model {id} identified, enqueing for processing')
            return True
        else:
            last_checked =  datetime.strptime(response['Item']['modified_date'], DATE_FORMAT) 
            if last_checked == latest_modification_date:
                logger.info(f"We're up to date with analysis for {id}")
                return False
            elif last_checked < latest_modification_date:
                logger.info(f"New version detected for {id}!")
                return True

    except Exception as e:
        logging.error(f'We had some trouble with dynamoDB: {e}')


def send_sqs(message, queue):
    sqs = boto3.resource("sqs")
    sqs_queue = sqs.get_queue_by_name(QueueName=queue)
    groupid = "bhakti_updates"
    deduplicationid = hashlib.md5(
        (
            groupid + json.dumps(message) + datetime.now().strftime("%d%m%Y%H%M%S")
        ).encode("utf-8")
    ).hexdigest()
    response = sqs_queue.send_message(
        MessageBody=message,
        MessageGroupId=groupid,
        MessageDeduplicationId=deduplicationid,
    )
    return response

def handler(event, context):
    logger.info("request: {}".format(json.dumps(event)))

    api_token = get_api_token()
    url = "https://huggingface.co/api/models/?full=full"
    scanning = True
    new_models = False

    try:
        next_url = scanPublicModels(url, api_token, 'keras_metadata.pb')
        while scanning == True:
            if 'huggingface' in next_url:
                next_url = scanPublicModels(next_url, api_token, 'keras_metadata.pb')
            else:
                scanning = False
     
        with open('/tmp/kerasFriends-keras_metadata.pb.txt', 'r') as results:
            current_keras_models = []
            for line in results.readlines():
                current_model = json.loads(line)
                current_keras_models.append(current_model)
        
        for model in current_keras_models:
            lastModified = model['lastModified'] 
            logger.info(f'model: {model["modelId"]} last modified on {lastModified}')
            update_needed = check_if_model_updated(model['id'], lastModified)
            if update_needed:
                new_models = True
                model['bhakti_request_date'] = datetime.now().strftime(DATE_FORMAT)
                logger.info(f'send_sqs_message with {model}')
                if len(model['siblings']) > 100:
                    model['siblings'] = 'too_many_files'
                    continue
                send_sqs(json.dumps(model), WORKING_QUEUE)

        if new_models:
            ec2 = boto3.client('ec2', region_name=AWS_REGION)
            instance = ec2.run_instances(
                ImageId=EC2_AMI,
                InstanceType="g4dn.xlarge",
                UserData=get_user_data(f'{ANALYSIS_BUCKET}/{ANALYSIS_PATH}'),
                IamInstanceProfile={ 'Arn': INSTANCE_PROFILE_ARN },
                InstanceInitiatedShutdownBehavior='terminate',
                KeyName='bhakti-ssh-key',
                MinCount=1,
                MaxCount=1
            )

            instance_data = {
                'status_code': instance['ResponseMetadata']['HTTPStatusCode']
            }

            if instance['ResponseMetadata']['HTTPStatusCode'] == 200:
                logger.info('Started an EC2 instance for analysis...')
                instance_data['instance_id'] = instance['Instances'][0]['InstanceId']
            else:
                logger.error('EC2 instance failed to launch')
                instance_data['instance_id'] = 'N/A, FAILED'

            logger.info(instance_data)
    
    except Exception as e:
        logger.error(e)