import json
from pathlib import Path
import logging
import requests
from tensorflow.python.keras.protobuf.saved_metadata_pb2 import SavedMetadata
from optparse import OptionParser
from datetime import datetime
import os
import dis
import codecs
import marshal
import base64
import string
import sys
import h5py
import shutil
from typing import Union, Dict, Any
from collections.abc import Generator

# output config
logger = logging.getLogger()
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


def gather_file(remote_model: str, api_token: str, directory: str) -> Union[Path, str]:
    """Attempts to assess a repo on huggingface and download any h5 or keras_metadata.pb 
    files found within it. Returns either an error string or a Path object.
    """
    filename = ""
    pb_filename = ""
    h5_filename = ""
    api_token = api_token
    url = f"https://huggingface.co/api/models/?id={remote_model}&full=full"

    headers = {"Authorization": f"Bearer {api_token}"}

    response = requests.request("GET", url, headers=headers)
    hf_model = response.json()

    for file in hf_model[0]["siblings"]:
        if "keras_metadata.pb" in file["rfilename"]:
            pb_filename = file["rfilename"]
        elif file["rfilename"].endswith(".h5"):
            h5_filename = file["rfilename"]

    if pb_filename:
        filename = pb_filename
    elif h5_filename:
        filename = h5_filename

    if filename:
        downloadLoc = Path(f"{directory}/{remote_model}/{filename}")
        downloadLoc.parent.mkdir(parents=True, exist_ok=True)
        downloadLink = f"https://huggingface.co/{remote_model}/resolve/main/{filename}"
        logger.info((f"Attempting to download: {downloadLink}"))
        try:
            with requests.get(downloadLink, headers=headers, stream=True) as r:
                if response.status_code == 401:
                    logger.error(
                        f"!!! Unfortunately, we're not authorized to retrieve {remote_model}"
                    )
                    downloadLoc = "UNAUTHORIZED"
                elif response.status_code == 200:
                    with open(downloadLoc, "wb") as resultFile:
                        shutil.copyfileobj(r.raw, resultFile)
                        logger.info((f"Wrote file to {downloadLoc}"))

        except Exception as e:
            logger.error(
                "!!! There was an issue downloading the file from huggingface! "
            )
            logger.error(e)

        return downloadLoc

    else:
        logger.info("Couldn't find a keras metadata file for this repo!")


def check_pb_for_code(local_file: Path, id: str) -> Dict[str, Any]:
    """Looks for the presence of a lambda layer within a keras_metadata.pb metadata file. 
    If a layer is found, attempts to pull out the embedded code. Returns a dictionary
    describing the model assessed. 
    """
    metadata = {"id": id, "type": "pb"}
    saved_metadata = SavedMetadata()
    logger.info((f"Checking {local_file} for keras lambda layer"))
    try:
        with open(local_file, "rb") as f:
            saved_metadata.ParseFromString(f.read())
        lambda_code = [
            layer["config"]["function"]["items"][0]
            for layer in [
                json.loads(node.metadata)
                for node in saved_metadata.nodes
                if node.identifier == "_tf_keras_layer"
            ]
            if layer["class_name"] == "Lambda"
        ]
        for code in lambda_code:
            logger.info((f"Found code in {local_file}: "))
            logger.info((f"CODE: {code}"))
        code = lambda_code[0]
        metadata["extracted_encoded_code"] = code
        metadata["contains_code"] = True
        return metadata
    # If we don't find a lambda layer, the above check will give an IndexError that we can assume
    # that the model does not contain a Lambda layer
    except IndexError as ie:
        metadata["contains_code"] = False
        logger.info((f"Didn't find code in {local_file}"))
        return metadata
    except Exception as e:
        logger.info((f"We had a non-index error analyzing {local_file} : {e}"))
        return metadata


def check_h5_for_code(local_file: str, id: str) -> Dict[str, Any]:
    """Looks for the presence of a lambda layer within an h5 model file. 
    If a layer is found, attempts to pull out the embedded code. Definitely
    will only work for Keras Tensorflow models saved using .save().
    Returns a dictionary describing the model assessed. 
    """
    metadata = {"id": id, "type": "h5"}
    logger.info((f"********* Checking {local_file} for keras lambda layer *********"))
    try:
        with h5py.File(local_file, "r") as f:
            # models saved with .save will contain a "model_config" attribute. Keras documentation
            # encourages this saving method in that this is the most consistent way to embed serialized code
            if "model_config" in list(f.attrs.keys()):
                try:
                    lambda_code = [
                        layer.get("config", {}).get("function", {})
                        for layer in json.loads(f.attrs["model_config"])["config"][
                            "layers"
                        ]
                        if layer["class_name"] == "Lambda"
                    ]
                    code = lambda_code[0][0]
                    logger.info((f"Found code in {local_file}: "))
                    logger.info((f"CODE: {code}"))
                    metadata["contains_code"] = True
                    metadata["extracted_encoded_code"] = code
                    return metadata
                except IndexError as ie:
                    logging.info(f"Didn't find code in {local_file}")
                    metadata["contains_code"] = False
                    return metadata
            else:
                metadata["contains_code"] = False
                logging.info(
                    f"!!! Unfortunately, {local_file} was not saved with an extractable model config"
                )
                return metadata
    except KeyError as ke:
        logging.info(
            f"!!! Unfortunately, {local_file} was not saved in a way for easy config extraction {ke}"
        )
        return metadata
    except Exception as e:
        logging.error(f"!!! We had a non-index error analyzing {local_file} : {e}")
    return metadata


def strings(encoded_code: bytes, min=4) -> Generator[str, None, None]:
    """
    Attempts to find printable strings >= 4 characters in length. Approximates
    Unix strings capability, but a lot more brittle. 
    """
    try:
        encoded_code = encoded_code.decode("latin1")
    except UnicodeDecodeError as e:
        logger.error("Unable to decode blob as text!")
    result = ""
    for c in encoded_code:
        if c in string.printable:
            result += c
            continue
        if len(result) >= min:
            yield result
        result = ""
    if len(result) >= min: 
        yield result


def main():
    class BhaktiParser(OptionParser):
        def format_epilog(self, formatter):
            return self.epilog

    usage = "usage: %prog -m author/model -r '/local/results/file' -a 'hf_api_key'"
    epilog = """Information:
    - Either a huggingface repo or a local model file is required.
    - Local files should be either Tensorflow models using keras saved in .h5 or keras_metadata.pb metadata files.
    - Unusual huggingface repo structures might behave oddly.
    - Not specifying a results file will result in results being written to std out.
    - Requesting a huggingface model without specifying a directory will write the file to the working directory
    
Examples:
    checkModel.py -m 'author/model' -r '/path/to/local/results/file' -d '/path/to/download/models' -a 'hugging_face_api_key' -c 'True'
    checkModel.py -f '/path/to/local/model' -r '/path/to/local/results/file'"""
    parser = BhaktiParser(usage=usage, epilog=epilog)
    parser.add_option(
        "-m",
        "--model",
        dest="remote_model",
        help="huggingface repo to assess",
        metavar="author/repo",
    )
    parser.add_option(
        "-f",
        "--file",
        dest="local_model",
        help="local model file to assess",
        metavar="/path/to/model",
    )
    parser.add_option(
        "-r",
        "--results_file",
        dest="results_file",
        help="flat file to write results, otherwise results are printed to stdout",
        metavar="/path/to/file",
    )
    parser.add_option(
        "-d",
        "--dir",
        dest="dir",
        metavar="/path/to/working/dir",
        help="local directory to store models downloaded from huggingface",
    )
    parser.add_option(
        "-a",
        "--api_key",
        dest="hf_api_key",
        metavar="hf_{...}",
        help="api token to use to interact with huggingface",
    )
    parser.add_option(
        "-c",
        "--clean_up",
        dest="clean_up",
        metavar="False",
        help="Set to true if you want to delete models that are downloaded",
        default="False",
    )

    (options, args) = parser.parse_args()

    if options.remote_model and options.local_model:
        parser.error("specify either a local file or remote repo, but not both :)")

    if not options.remote_model and not options.local_model:
        parser.error(
            "Please specify at least one model to analyze using either [-m|--model] (remote) or [-f|--file] (local)"
        )

    if options.remote_model and not options.dir:
        logger.info(
            "No results directory specified, fetching remote model to working directory..."
        )

    if not options.hf_api_key and options.remote_model:
        logger.info(
            "No api key provided but requesting model, trying to download without authorization"
        )
        hf_api_key = ""
    elif options.hf_api_key:
        hf_api_key = options.hf_api_key

    if options.local_model:
        local_model = options.local_model
        results = {}
        if not local_model.endswith(".h5") and local_model.endswith(".pb"):
            results = check_pb_for_code(local_model, local_model)
        elif local_model.endswith(".h5"):
            results = check_h5_for_code(local_model, local_model)

    elif options.remote_model:
        remote_model = options.remote_model
        api_token = hf_api_key
        if options.dir:
            directory = options.dir
        else:
            directory = "."
        downloaded_file = gather_file(remote_model, api_token, directory)
        file_path = str(downloaded_file)
        if downloaded_file != "UNAUTHORIZED":
            if not file_path.endswith(".h5") and file_path.endswith(".pb"):
                results = check_pb_for_code(downloaded_file, remote_model)
            elif file_path.endswith(".h5"):
                results = check_h5_for_code(downloaded_file, remote_model)

    if code := results.get("extracted_encoded_code"):
        logger.info(
            f"********* Trying to disassemble extracted code layer in {results['id']}: *********"
        )
        try:
            dis.dis(marshal.loads(codecs.decode(code.encode("ascii"), "base64")))
        except Exception as e:
            logger.error(f"!!! Unfortunately, dis struggled with {results['id']}: {e}")
        logger.info(
            f"********* Attempting to find strings for {results['id']}: *********"
        )
        decoded_code = base64.b64decode(code)
        sl = list(strings(decoded_code))
        if len(sl) > 0:
            results["string_list"] = sl
            logger.info(f"Found strings in {results['id']}:")
            logger.info(f"STRINGS: {sl}")
        else:
            logger.info(f"Could not find any printable strings in {results['id']}!")

    if options.results_file:
        results_file = options.results_file
        results_file = Path(results_file)
        results_file.parent.mkdir(parents=True, exist_ok=True)
        with open(results_file, "a") as f:
            f.write(json.dumps(results))
            f.write("\n")
    else:
        logger.info(
            "********* No result file specified, printing results to std out: *********"
        )
        logger.info(results)

    clean_up = options.clean_up
    if clean_up.lower() in ["true", "1"] and options.remote_model:
        os.remove(downloaded_file)
        parent_dir = remote_model.split("/")[0]
        if options.dir:
            directory = options.dir.rstrip("/")
            os.rmdir(f"{directory}/{remote_model}")
            os.rmdir(f"{directory}/{parent_dir}")
        elif options.remote_model:
            os.rmdir(remote_model)
            os.rmdir(parent_dir)


if __name__ == "__main__":
    main()
