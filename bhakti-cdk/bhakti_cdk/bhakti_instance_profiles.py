from aws_cdk import (
    # Duration,
    Stack,
    aws_secretsmanager,
    aws_logs as logs,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3_assets as assets,
)
from constructs import Construct
from typing import Optional

class InstanceProfiles(Stack): 
    def __init__(self, scope: Construct, 
        construct_id: str, 
        hf_token: aws_secretsmanager.Secret,
        sg_id: ec2.SecurityGroup, 
        script_asset: assets.Asset, 
        **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Add any AWS access you need on your EC2 instance as PolicyStatements in this PolicyDocument
        bhakti_access = iam.PolicyDocument(
            statements=[iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
                resources=[hf_token.secret_arn]
            )]
        )

        bhakti_ec2_access_policy = iam.Policy(
            self, "bhakti_ec2_access_policy", 
            document=bhakti_access
        )

        bhakti_role = iam.Role(
            self, "bhakti_role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="EC2 instance role for Bhakti analysis instances"
        )

        bhakti_role.attach_inline_policy(bhakti_ec2_access_policy)

        bhakti_instance_profile = iam.CfnInstanceProfile(
            self, "bhakti_instance_profile",
            roles=[bhakti_role.role_name]   
        )

        bhakti_keypair = ec2.KeyPair(self, "bhakti_ssh_key", 
            key_pair_name='bhakti-ssh-key'
        )

        #bundle analysis script in s3 for use in ec2 user data 
        #asset = assets.Asset(
        #    self, "file_asset",
        #    path=("./analysis/checkModel.py")
        #)

        bhakti_user_data = ec2.UserData.for_linux()

        local_path = bhakti_user_data.add_s3_download_command(
            bucket=script_asset.bucket,
            bucket_key = script_asset.s3_object_key,
        )

        check_model = f'{local_path} -d /home/ec2-user/analysis'

        bhakti_user_data.add_execute_file_command(
            file_path='/usr/bin/unzip',
            arguments=check_model
        )

        script_asset.grant_read(bhakti_role)

        # This template does not include any default security groups-- this means you won't be able to access it 
        # until you set-up at least an ssh-allow security group within EC2. 
    
        if sg_id:
            bhakti_analysis = ec2.LaunchTemplate(
            self, "ec2_template",
            launch_template_name="bhakti_model_analysis",
            machine_image=ec2.MachineImage.lookup(name='Deep*',filters={'image-id':['ami-0b28c78d9f575dfa1']}, owners=["amazon"]),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.G4DN, ec2.InstanceSize.XLARGE),
            key_pair=bhakti_keypair,
            user_data=bhakti_user_data,
            role=bhakti_role,
            security_group=sg_id,
            instance_initiated_shutdown_behavior=ec2.InstanceInitiatedShutdownBehavior.TERMINATE,
        )
        else:
            bhakti_analysis = ec2.LaunchTemplate(
            self, "ec2_template",
            launch_template_name="bhakti_model_analysis",
            machine_image=ec2.MachineImage.lookup(name='Deep*',filters={'image-id':['ami-0b28c78d9f575dfa1']}, owners=["amazon"]),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.G4DN, ec2.InstanceSize.XLARGE),
            key_pair=bhakti_keypair,
            user_data=bhakti_user_data,
            role=bhakti_role,
            instance_initiated_shutdown_behavior=ec2.InstanceInitiatedShutdownBehavior.TERMINATE, 
        )