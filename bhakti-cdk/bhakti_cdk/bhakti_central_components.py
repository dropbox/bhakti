from aws_cdk import (
    # Duration,
    Stack,
    aws_secretsmanager,
    aws_logs as logs,
    aws_s3_assets as assets,
    aws_ec2 as ec2
)
from constructs import Construct
from typing import Optional

class BhaktiShared(Stack):

    def __init__(self, scope: Construct, construct_id: str, sg_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        huggingface_token = aws_secretsmanager.Secret(
            self,
            "huggingface_token",
            secret_name="huggingface_api_token"
        )

        #bundle analysis script in s3 for use in ec2 user data 
        s3_script_asset = assets.Asset(
            self, "file_asset",
            path=("./analysis/")
        )
        self._asset = s3_script_asset
        self._token = huggingface_token

        if sg_id:
            security_group = ec2.SecurityGroup.from_security_group_id(self, 'sg', sg_id)
            self._security_group = security_group
        else:
            self._security_group = None

    @property
    def hf_token(self) -> aws_secretsmanager.Secret:
        return self._token

    @property
    def script_asset(self) -> assets.Asset:
        return self._asset
    
    @property
    def sg(self) -> Optional[ec2.SecurityGroup]:
        return self._security_group    

