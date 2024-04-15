#!/usr/bin/env python3
import os

import aws_cdk as cdk

from bhakti_cdk.bhakti_monitoring_stack import MonitoringStack
from bhakti_cdk.bhakti_instance_profiles import InstanceProfiles
from bhakti_cdk.bhakti_central_components import BhaktiShared


app = cdk.App()
deploy_type = app.node.try_get_context("deploy_type")
deploy_region = app.node.try_get_context("deploy_region")
deploy_account = app.node.try_get_context("deploy_account")
env = cdk.Environment(account=f'{deploy_account}', region=f'{deploy_region}')
sg_id = app.node.try_get_context("sg_id")

print(deploy_type)
print(deploy_region)
print(deploy_account)
print(env)

if not sg_id:
    sg_id = None

shared_resources = BhaktiShared(app, "BhaktiShared", sg_id=sg_id, env=env)
if deploy_type == 'monitoring':
    MonitoringStack(app, "MonitoringStack", 
        hf_token=shared_resources.hf_token,
        script_asset=shared_resources.script_asset, 
        env=env,)
elif deploy_type == 'instance_profile':
    InstanceProfiles(app, "InstanceProfileStack", 
        hf_token=shared_resources.hf_token, 
        script_asset=shared_resources.script_asset, 
        sg_id=shared_resources.sg,  
        env=env,
    )

app.synth()
