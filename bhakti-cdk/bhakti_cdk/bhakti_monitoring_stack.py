from aws_cdk import (
    aws_s3_assets as assets,
    Stack,
    aws_secretsmanager,
    aws_logs as logs,
    aws_dynamodb,
    aws_s3 as s3,
    aws_sqs as sqs,
    aws_iam as iam,
    Duration,
    aws_lambda,
    aws_events,
    aws_events_targets,
)
from constructs import Construct

class MonitoringStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, hf_token: aws_secretsmanager.Secret, script_asset: assets.Asset, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        huggingface_token = hf_token
        bhakti_log_group = logs.LogGroup(self, 'bhakti_logs')

        status_table = aws_dynamodb.TableV2(self, 'status_table',
            partition_key=aws_dynamodb.Attribute(
                name='repo',
                type=aws_dynamodb.AttributeType.STRING
            ),
            sort_key=aws_dynamodb.Attribute(
                name='version',
                type=aws_dynamodb.AttributeType.STRING
            )
        )
        status_table.add_global_secondary_index(
            index_name="models_with_code", 
            partition_key=aws_dynamodb.Attribute(
                name='extracted_encoded_code', 
                type=aws_dynamodb.AttributeType.STRING))

        bhakti_analysis_bucket = s3.Bucket(
            self, 'bhakti_analysis_bucket',
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        monitoring_queue = sqs.Queue(
            self,
            "monitoring_queue",
            queue_name="bhakti_monitoring_queue.fifo",
            visibility_timeout=Duration.seconds(660),
            fifo=True,
        )

        bhakti_automated_role = iam.Role(
            self, "bhakti_automated_role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="EC2 instance role for automated Bhakti analysis instances"
        )

        monitoring_execution = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:DeleteItem", "dynamodb:PutItem"],
                    resources=[status_table.table_arn],
                ),
                iam.PolicyStatement(
                    actions=["sqs:SendMessage", "sqs:GetQueueUrl"],
                    resources=[monitoring_queue.queue_arn],
                ),
                iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
                    resources=[huggingface_token.secret_arn],
                ),
                iam.PolicyStatement(
                    actions=["s3:putItem"],
                    resources=[f"{bhakti_analysis_bucket.bucket_arn}/*"],
                ),
                iam.PolicyStatement(
                    actions=["iam:PassRole"],
                    resources=[bhakti_automated_role.role_arn],
                ),
                iam.PolicyStatement(
                    actions=["ec2:RunInstances", "ec2:CreateTags"],
                    resources=[
                        f"arn:aws:ec2:{self.region}:{self.account}:instance/*",
                        f"arn:aws:ec2:{self.region}:{self.account}:image/ami-0b28c78d9f575dfa1",
                        f"arn:aws:ec2:{self.region}:{self.account}:network-interface/*",
                        f"arn:aws:ec2:{self.region}:{self.account}:security-group/*",
                        f"arn:aws:ec2:{self.region}:{self.account}:subnet/subnet-*",
                        f"arn:aws:ec2:{self.region}:{self.account}:volume/*",
                        f"arn:aws:ec2:{self.region}::image/ami-0b28c78d9f575dfa1",
                    ],
                ),
            ]
        )
        monitoring_execution_policy = iam.Policy(
            self, "monitoring_execution_policy", document=monitoring_execution
        )

        asset_bucket = s3.Bucket.from_bucket_name(self, 'script_bucket', script_asset.s3_bucket_name)

        bhakti_analysis_policy_statement = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(
                    actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:DeleteItem", "dynamodb:PutItem"],
                    resources=[status_table.table_arn],
                ),
                iam.PolicyStatement(
                    actions=["s3:getItem"],
                    resources=[f"{asset_bucket.bucket_arn}/*"]
                ),
                iam.PolicyStatement(
                    actions=["s3:putItem"],
                    resources=[f"{bhakti_analysis_bucket.bucket_arn}/*"]
                ),
                iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
                    resources=[hf_token.secret_arn]
                ),
                iam.PolicyStatement(
                    actions=["sqs:GetQueueUrl", "sqs:ReceiveMessage", "sqs:DeleteMessage"],
                    resources=[monitoring_queue.queue_arn]
                )
            ]
        )

        bhakti_analysis_policy = iam.Policy(self, "bhakti_analysis_policy", document=bhakti_analysis_policy_statement)

        bhakti_automated_role.attach_inline_policy(bhakti_analysis_policy)

        bhakti_instance_profile = iam.CfnInstanceProfile(
            self, "bhakti_automated_instance_profile",
            roles=[bhakti_automated_role.role_name]   
        )

        monitoring_lambda = aws_lambda.Function(
            self,
            "monitoring_lambda",
            code=aws_lambda.Code.from_asset(
                "lambda",
                bundling={
                    "image":aws_lambda.Runtime.PYTHON_3_12.bundling_image,
                    "command": [
                        'bash','-c',
                        'pip install -r requirements.txt -t /asset-output && cp -au . /asset-output'
                    ],
                },
            ),
            handler="monitoring_lambda.handler",
            timeout=Duration.seconds(900),
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            memory_size=3072,
            log_group=bhakti_log_group,
            environment={
                'DYNAMO_TABLE' : status_table.table_name,
                'WORKING_QUEUE' : monitoring_queue.queue_name,
                'HF_TOKEN' : huggingface_token.secret_name,
                'AWS_REG' : self.region,
                'INSTANCE_PROFILE_ARN' : bhakti_instance_profile.attr_arn,
                'LOGGING_BUCKET' : bhakti_analysis_bucket.bucket_name,
                'ANALYSIS_BUCKET' : script_asset.s3_bucket_name,
                'ANALYSIS_PATH' : script_asset.s3_object_key,
            }
        )

        monitoring_lambda.role.attach_inline_policy(monitoring_execution_policy)
        script_asset.grant_read(bhakti_automated_role)

        keras_monitoring_event_rule = aws_events.Rule(
            self,
            "keras_monitoring_event_rule",
            schedule=aws_events.Schedule.cron(
                hour = "1",
                minute = "0",
            )
        )

        keras_monitoring_event_rule.add_target(aws_events_targets.LambdaFunction(monitoring_lambda))

