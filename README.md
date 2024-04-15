# Bhakti

[Bhakti](https://finalfantasy.fandom.com/wiki/Bhakti) is a conglomeration of analysis tools to look at certain types of machine learning models for the presence of a code execution layer. There are three main components:

- Analysis script(s)
- Amazon CDK to create an AWS investigation lab or to stand-up automated model monitoring
- Yara rules that can be used to identify risky models 

## Background

This little repo is scrappy tooling that resulted from a threat hunt that the Dropbox Threat Intelligence team conducted across huggingface in 2023 and into 2024. This work coalesced after [@5stars217](https://github.com/5stars217) began to publish his work on malicious models in the keras space (see [On Malicious Models](https://5stars217.github.io/2023-03-30-on-malicious-models/)). Currently, all analysis is restricted to Tensorflow models using Keras that leverage a lambda layer as a vehicle for arbitrary code execution on a victim system.  

## Analysis scripts

[Analysis Scripts](analysis/)
- `checkModel.py` is designed to assess either a local model or a huggingface repo for a lambda layer. It supports `.h5` and `keras_metadata.pb` formats; it attempts to dump any code found within any identified layers in these kinds of files. 

## YARA rules
[YARA Rules](yara/)
- `keras-requests.yara` flags on any model using the requests library

## CDK Stuff
[CDK Things](bhakti-cdk/)

Parameterizing CDK and making it beautiful and portable is not really my forte, but I've done my best. There's a whole additional [README.md](bhakti-cdk/README.md) file in the cdk sub-folder with more information about standing up this infrastructure in your own account. **AWS isn't free**, please configure your account with appropriate billing alarms so you're not taken aback by anything these stacks might do trying to be a good little robots. 

- `$monitoring_stack` will attempt to deploy a monitoring solution in a bootstrapped AWS account. 
- `$analysis_stack` will attempt to stand-up an ec2 launch template in a bootstrapped AWS account to use for ML malware analysis

## License

Unless otherwise noted:

```
Copyright (c) 2023-2024 Dropbox, Inc

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```