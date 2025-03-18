# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import StringIO
from typing import Optional
from datetime import datetime
import asyncio
import logging
from fastapi.middleware.cors import CORSMiddleware
import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'functions')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared_code')))
import json
import urllib.parse
import pandas as pd
import pydantic
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, Form
from fastapi.responses import RedirectResponse, StreamingResponse
import openai
#from approaches.comparewebwithwork import CompareWebWithWork
#from approaches.compareworkwithweb import CompareWorkWithWeb
#from approaches.chatreadretrieveread import ChatReadRetrieveReadApproach
#from approaches.chatwebretrieveread import ChatWebRetrieveRead
#from approaches.gpt_direct_approach import GPTDirectApproach
#from approaches.approach import Approaches
from azure.identity import ManagedIdentityCredential, AzureAuthorityHosts, DefaultAzureCredential, get_bearer_token_provider
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
from azure.search.documents import SearchClient
from azure.storage.blob import BlobServiceClient, ContentSettings
#from approaches.mathassistant import(
 #   generate_response,
  #  process_agent_response,
   # stream_agent_responses
#)
#from approaches.tabulardataassistant import (
 #   refreshagent,
  #  save_df,
   # process_agent_response as td_agent_response,
   # process_agent_scratch_pad as td_agent_scratch_pad,
    #get_images_in_temp
#)
from shared_code.status_log import State, StatusClassification, StatusLog
from azure.cosmos import CosmosClient


# === ENV Setup ===

ENV = {
    "AZURE_BLOB_STORAGE_ACCOUNT": "infoasststorejxrgv",
    "USE_AZURE_OPENAI_EMBEDDINGS": False,
    "AZURE_BLOB_STORAGE_ENDPOINT": "https://infoasststorejxrgv.blob.core.windows.net/",
    "AZURE_BLOB_STORAGE_CONTAINER": "content",
    "AZURE_BLOB_STORAGE_UPLOAD_CONTAINER": "upload",
    "AZURE_SEARCH_SERVICE": "infoasst-search-jxrgv",
    "AZURE_SEARCH_SERVICE_ENDPOINT": "https://infoasst-search-jxrgv.search.windows.net",
    "AZURE_SEARCH_INDEX": "gptkbindex",
    "AZURE_SEARCH_AUDIENCE": "",
    "USE_SEMANTIC_RERANKER": "true",
    "AZURE_OPENAI_SERVICE": "infoasst-aoai-uftbv-eu",
    "AZURE_OPENAI_RESOURCE_GROUP": "infoasst-myworkspace-ircc2",
    "AZURE_OPENAI_ENDPOINT": "https://infoasst-aoai-uftbv-eu.openai.azure.com/",
    "AZURE_OPENAI_AUTHORITY_HOST": "AzureCloud",
    "AZURE_OPENAI_CHATGPT_DEPLOYMENT": "gpt-35-turbo-16k",
    "AZURE_OPENAI_CHATGPT_MODEL_NAME": "gpt-35-turbo-16k",
    "AZURE_OPENAI_CHATGPT_MODEL_VERSION": "0613",
    "EMBEDDING_DEPLOYMENT_NAME": "text-embedding-ada-002",
    "AZURE_OPENAI_EMBEDDINGS_MODEL_NAME": "text-embedding-ada-002",
    "AZURE_OPENAI_EMBEDDINGS_VERSION": "2",
    "AZURE_SUBSCRIPTION_ID": "d7a55bca-4abb-4720-9ffe-133a4755d5b7",
    "AZURE_ARM_MANAGEMENT_API": "https://management.azure.com",
    "CHAT_WARNING_BANNER_TEXT": "",
    "APPLICATION_TITLE": "",
    "KB_FIELDS_CONTENT": "content",
    "KB_FIELDS_PAGENUMBER": "pages",
    "KB_FIELDS_SOURCEFILE": "file_name",
    "KB_FIELDS_CHUNKFILE": "chunk_file",
    "COSMOSDB_URL": "https://infoasst-cosmos-jxrgv.documents.azure.com:443/",
    "COSMOSDB_LOG_DATABASE_NAME": "statusdb",
    "COSMOSDB_LOG_CONTAINER_NAME": "statuscontainer",
    "QUERY_TERM_LANGUAGE": "English",
    "TARGET_EMBEDDINGS_MODEL": "BAAI/bge-small-en-v1.5",
    "ENRICHMENT_APPSERVICE_URL": "enrichment",
    "TARGET_TRANSLATION_LANGUAGE": "en",
    "AZURE_AI_ENDPOINT": "",
    "AZURE_AI_LOCATION": "",
    "BING_SEARCH_ENDPOINT": "https://api.bing.microsoft.com/",
    "BING_SEARCH_KEY": "",
    "ENABLE_BING_SAFE_SEARCH": "true",
    "ENABLE_WEB_CHAT": "false",
    "ENABLE_UNGROUNDED_CHAT": "false",
    "ENABLE_MATH_ASSISTANT": "false",
    "ENABLE_TABULAR_DATA_ASSISTANT": "false",
    "MAX_CSV_FILE_SIZE": "7",
   "LOCAL_DEBUG": "true",
   "AZURE_AI_CREDENTIAL_DOMAIN": "cognitiveservices.azure.com"
    }

for key, value in ENV.items():
    new_value = os.getenv(key)
    if new_value is not None:
        ENV[key] = new_value
    elif value is None:
        raise ValueError(f"Environment variable {key} not set")

str_to_bool = {'true': True, 'false': False}

log = logging.getLogger("uvicorn")
log.setLevel('DEBUG')
log.propagate = True

class StatusResponse(pydantic.BaseModel):
    """The response model for the health check endpoint"""
    status: str
    uptime_seconds: float
    version: str

start_time = datetime.now()

IS_READY = False

DF_FINAL = None
# Used by the OpenAI SDK
openai.api_type = "azure"
openai.api_base = ENV["AZURE_OPENAI_ENDPOINT"]
if ENV["AZURE_OPENAI_AUTHORITY_HOST"] == "AzureUSGovernment":
    AUTHORITY = AzureAuthorityHosts.AZURE_GOVERNMENT
else:
    AUTHORITY = AzureAuthorityHosts.AZURE_PUBLIC_CLOUD
openai.api_version = "2024-02-01"
# When debugging in VSCode, use the current user identity to authenticate with Azure OpenAI,
# Cognitive Search and Blob Storage (no secrets needed, just use 'az login' locally)
# Use managed identity when deployed on Azure.
# If you encounter a blocking error during a DefaultAzureCredntial resolution, you can exclude
# the problematic credential by using a parameter (ex. exclude_shared_token_cache_credential=True)
if ENV["LOCAL_DEBUG"] == "true":
    azure_credential = DefaultAzureCredential(authority=AUTHORITY)
else:
    azure_credential = ManagedIdentityCredential(authority=AUTHORITY)
# Comment these two lines out if using keys, set your API key in the OPENAI_API_KEY
# environment variable instead
openai.api_type = "azure_ad"
token_provider = get_bearer_token_provider(azure_credential,
                                           f'https://{ENV["AZURE_AI_CREDENTIAL_DOMAIN"]}/.default')
openai.azure_ad_token_provider = token_provider
#openai.api_key = ENV["AZURE_OPENAI_SERVICE_KEY"]

# Setup StatusLog to allow access to CosmosDB for logging
statusLog = StatusLog(
    ENV["COSMOSDB_URL"],
    azure_credential,
    ENV["COSMOSDB_LOG_DATABASE_NAME"],
    ENV["COSMOSDB_LOG_CONTAINER_NAME"]
)

# Set up clients for Cognitive Search and Storage
search_client = SearchClient(
    endpoint=ENV["AZURE_SEARCH_SERVICE_ENDPOINT"],
    index_name=ENV["AZURE_SEARCH_INDEX"],
    credential=azure_credential,
    audience=ENV["AZURE_SEARCH_AUDIENCE"]
)

blob_client = BlobServiceClient(
    account_url=ENV["AZURE_BLOB_STORAGE_ENDPOINT"],
    credential=azure_credential,
)
blob_container = blob_client.get_container_client(ENV["AZURE_BLOB_STORAGE_CONTAINER"])
blob_upload_container_client = blob_client.get_container_client(
                                    os.environ["AZURE_BLOB_STORAGE_UPLOAD_CONTAINER"])

MODEL_NAME = ''
MODEL_VERSION = ''

# Set up OpenAI management client
openai_mgmt_client = CognitiveServicesManagementClient(
    credential=azure_credential,
    subscription_id=ENV["AZURE_SUBSCRIPTION_ID"],
    base_url=ENV["AZURE_ARM_MANAGEMENT_API"],
    credential_scopes=[ENV["AZURE_ARM_MANAGEMENT_API"] + "/.default"])

deployment = openai_mgmt_client.deployments.get(
    resource_group_name=ENV["AZURE_OPENAI_RESOURCE_GROUP"],
    account_name=ENV["AZURE_OPENAI_SERVICE"],
    deployment_name=ENV["AZURE_OPENAI_CHATGPT_DEPLOYMENT"])

MODEL_NAME = deployment.properties.model.name
MODEL_VERSION = deployment.properties.model.version

if str_to_bool.get(ENV["USE_AZURE_OPENAI_EMBEDDINGS"]):
    embedding_deployment = openai_mgmt_client.deployments.get(
        resource_group_name=ENV["AZURE_OPENAI_RESOURCE_GROUP"],
        account_name=ENV["AZURE_OPENAI_SERVICE"],
        deployment_name=ENV["EMBEDDING_DEPLOYMENT_NAME"])

    EMBEDDING_MODEL_NAME = embedding_deployment.properties.model.name
    EMBEDDING_MODEL_VERSION = embedding_deployment.properties.model.version
else:
    EMBEDDING_MODEL_NAME = ""
    EMBEDDING_MODEL_VERSION = ""


IS_READY = True

# Create API
app = FastAPI(
    title="IA Web API",
    description="A Python API to serve as Backend For the Information Assistant Web App",
    version="0.1.0",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or set your frontend's URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False, response_class=RedirectResponse)
async def root():
    """Redirect to the index.html page"""
    return RedirectResponse(url="/index.html")

@app.get("/health", response_model=StatusResponse, tags=["health"])
def health():
    """Returns the health of the API

    Returns:
        StatusResponse: The health of the API
    """

    uptime = datetime.now() - start_time
    uptime_seconds = uptime.total_seconds()

    output = {"status": None, "uptime_seconds": uptime_seconds, "version": app.version}

    if IS_READY:
        output["status"] = "ready"
    else:
        output["status"] = "loading"

    return output

# ---------------------------
# REMOVED /chat ENDPOINT HERE
# REMOVED chat_approaches logic
# ---------------------------

@app.post("/getalluploadstatus")
async def get_all_upload_status(request: Request):
    """
    Get the status and tags of all file uploads in the last N hours.

    Parameters:
    - request: The HTTP request object.

    Returns:
    - results: The status of all file uploads in the specified timeframe.
    """
    json_body = await request.json()
    timeframe = json_body.get("timeframe")
    state = json_body.get("state")
    folder = json_body.get("folder")
    tag = json_body.get("tag")
    try:
        results = statusLog.read_files_status_by_timeframe(timeframe,
            State[state],
            folder,
            tag,
            os.environ["AZURE_BLOB_STORAGE_UPLOAD_CONTAINER"])

        # retrieve tags for each file
         # Initialize an empty list to hold the tags
        items = []
        cosmos_client = CosmosClient(url=statusLog._url,
                                     credential=azure_credential,
                                     consistency_level='Session')
        database = cosmos_client.get_database_client(statusLog._database_name)
        container = database.get_container_client(statusLog._container_name)
        query_string = "SELECT DISTINCT VALUE t FROM c JOIN t IN c.tags"
        items = list(container.query_items(
            query=query_string,
            enable_cross_partition_query=True
        ))

        # Extract and split tags
        unique_tags = set()
        for item in items:
            tags = item.split(',')
            unique_tags.update(tags)

    except Exception as ex:
        log.exception("Exception in /getalluploadstatus")
        raise HTTPException(status_code=500, detail=str(ex)) from ex
    return results

@app.post("/getfolders")
async def get_folders():
    """
    Get all folders.

    Parameters:
    - request: The HTTP request object.

    Returns:
    - results: list of unique folders.
    """
    try:
        blob_container = blob_client.get_container_client(os.environ["AZURE_BLOB_STORAGE_UPLOAD_CONTAINER"])
        # Initialize an empty list to hold the folder paths
        folders = []
        # List all blobs in the container
        blob_list = blob_container.list_blobs()
        # Iterate through the blobs and extract folder names and add unique values to the list
        for blob in blob_list:
            # Extract the folder path if exists
            folder_path = os.path.dirname(blob.name)
            if folder_path and folder_path not in folders:
                folders.append(folder_path)
    except Exception as ex:
        log.exception("Exception in /getfolders")
        raise HTTPException(status_code=500, detail=str(ex)) from ex
    return folders


@app.post("/deleteItems")
async def delete_Items(request: Request):
    """
    Delete a blob.

    Parameters:
    - request: The HTTP request object.

    Returns:
    - results: list of unique folders.
    """
    json_body = await request.json()
    full_path = json_body.get("path")
    # remove the container prefix
    path = full_path.split("/", 1)[1]
    try:
        blob_container = blob_client.get_container_client(os.environ["AZURE_BLOB_STORAGE_UPLOAD_CONTAINER"])
        blob_container.delete_blob(path)
        statusLog.upsert_document(document_path=full_path,
            status='Delete intiated',
            status_classification=StatusClassification.INFO,
            state=State.DELETING,
            fresh_start=False)
        statusLog.save_document(document_path=full_path)   

    except Exception as ex:
        log.exception("Exception in /delete_Items")
        raise HTTPException(status_code=500, detail=str(ex)) from ex
    return True


@app.post("/resubmitItems")
async def resubmit_Items(request: Request):
    """
    Resubmit a blob.

    Parameters:
    - request: The HTTP request object.

    Returns:
    - results: list of unique folders.
    """
    json_body = await request.json()
    path = json_body.get("path")
    # remove the container prefix
    path = path.split("/", 1)[1]
    try:
        blob_container = blob_client.get_container_client(os.environ["AZURE_BLOB_STORAGE_UPLOAD_CONTAINER"])
        # Read the blob content into memory
        blob_data = blob_container.download_blob(path).readall()
        
        submitted_blob_client = blob_container.get_blob_client(blob=path)
        blob_properties = submitted_blob_client.get_blob_properties()
        metadata = blob_properties.metadata
        blob_container.upload_blob(name=path, data=blob_data, overwrite=True, metadata=metadata)   
       
        
        

        # add the container to the path to avoid adding another doc in the status db
        full_path = os.environ["AZURE_BLOB_STORAGE_UPLOAD_CONTAINER"] + '/' + path
        statusLog.upsert_document(document_path=full_path,
                    status='Resubmitted to the processing pipeline',
                    status_classification=StatusClassification.INFO,
                    state=State.QUEUED,
                    fresh_start=False)
        statusLog.save_document(document_path=full_path)   

    except Exception as ex:
        log.exception("Exception in /resubmitItems")
        raise HTTPException(status_code=500, detail=str(ex)) from ex
    return True


@app.post("/gettags")
async def get_tags(request: Request):
    """
    Get all tags.

    Parameters:
    - request: The HTTP request object.

    Returns:
    - results: list of unique tags.
    """
    try:
        # Initialize an empty list to hold the tags
        items = []              
        cosmos_client = CosmosClient(url=statusLog._url, credential=azure_credential, consistency_level='Session')     
        database = cosmos_client.get_database_client(statusLog._database_name)               
        container = database.get_container_client(statusLog._container_name) 
        query_string = "SELECT DISTINCT VALUE t FROM c JOIN t IN c.tags"  
        items = list(container.query_items(
            query=query_string,
            enable_cross_partition_query=True
        ))           

        # Extract and split tags
        unique_tags = set()
        for item in items:
            tags = item.split(',')
            unique_tags.update(tags)                  
                
    except Exception as ex:
        log.exception("Exception in /gettags")
        raise HTTPException(status_code=500, detail=str(ex)) from ex
    return unique_tags

@app.post("/logstatus")
async def logstatus(request: Request):
    """
    Log the status of a file upload to CosmosDB.

    Parameters:
    - request: Request object containing the HTTP request data.

    Returns:
    - A dictionary with the status code 200 if successful, or an error
        message with status code 500 if an exception occurs.
    """
    try:
        json_body = await request.json()
        path = json_body.get("path")
        status = json_body.get("status")
        status_classification = StatusClassification[json_body.get("status_classification").upper()]
        state = State[json_body.get("state").upper()]

        statusLog.upsert_document(document_path=path,
                                  status=status,
                                  status_classification=status_classification,
                                  state=state,
                                  fresh_start=True)
        statusLog.save_document(document_path=path)

    except Exception as ex:
        log.exception("Exception in /logstatus")
        raise HTTPException(status_code=500, detail=str(ex)) from ex
    raise HTTPException(status_code=200, detail="Success")

@app.get("/getInfoData")
async def get_info_data():
    """
    Get the info data for the app.

    Returns:
        dict: A dictionary containing various information data for the app.
            - "AZURE_OPENAI_CHATGPT_DEPLOYMENT": The deployment information for Azure OpenAI ChatGPT.
            - "AZURE_OPENAI_MODEL_NAME": The name of the Azure OpenAI model.
            - "AZURE_OPENAI_MODEL_VERSION": The version of the Azure OpenAI model.
            - "AZURE_OPENAI_SERVICE": The Azure OpenAI service information.
            - "AZURE_SEARCH_SERVICE": The Azure search service information.
            - "AZURE_SEARCH_INDEX": The Azure search index information.
            - "TARGET_LANGUAGE": The target language for query terms.
            - "USE_AZURE_OPENAI_EMBEDDINGS": Flag indicating whether to use Azure OpenAI embeddings.
            - "EMBEDDINGS_DEPLOYMENT": The deployment information for embeddings.
            - "EMBEDDINGS_MODEL_NAME": The name of the embeddings model.
            - "EMBEDDINGS_MODEL_VERSION": The version of the embeddings model.
    """
    response = {
        "AZURE_OPENAI_CHATGPT_DEPLOYMENT": ENV["AZURE_OPENAI_CHATGPT_DEPLOYMENT"],
        "AZURE_OPENAI_MODEL_NAME": f"{MODEL_NAME}",
        "AZURE_OPENAI_MODEL_VERSION": f"{MODEL_VERSION}",
        "AZURE_OPENAI_SERVICE": ENV["AZURE_OPENAI_SERVICE"],
        "AZURE_SEARCH_SERVICE": ENV["AZURE_SEARCH_SERVICE"],
        "AZURE_SEARCH_INDEX": ENV["AZURE_SEARCH_INDEX"],
        "TARGET_LANGUAGE": ENV["QUERY_TERM_LANGUAGE"],
        "USE_AZURE_OPENAI_EMBEDDINGS": ENV["USE_AZURE_OPENAI_EMBEDDINGS"],
        "EMBEDDINGS_DEPLOYMENT": ENV["EMBEDDING_DEPLOYMENT_NAME"],
        "EMBEDDINGS_MODEL_NAME": f"{EMBEDDING_MODEL_NAME}",
        "EMBEDDINGS_MODEL_VERSION": f"{EMBEDDING_MODEL_VERSION}",
    }
    return response


@app.get("/getWarningBanner")
async def get_warning_banner():
    """Get the warning banner text"""
    response ={
            "WARNING_BANNER_TEXT": ENV["CHAT_WARNING_BANNER_TEXT"]
        }
    return response

@app.get("/getMaxCSVFileSize")
async def get_max_csv_file_size():
    """Get the max csv size"""
    response ={
            "MAX_CSV_FILE_SIZE": ENV["MAX_CSV_FILE_SIZE"]
        }
    return response

@app.post("/getcitation")
async def get_citation(request: Request):
    """
    Get the citation for a given file

    Parameters:
        request (Request): The HTTP request object

    Returns:
        dict: The citation results in JSON format
    """
    try:
        json_body = await request.json()
        citation = urllib.parse.unquote(json_body.get("citation"))    
        blob = blob_container.get_blob_client(citation).download_blob()
        decoded_text = blob.readall().decode()
        results = json.loads(decoded_text)
    except Exception as ex:
        log.exception("Exception in /getcitation")
        raise HTTPException(status_code=500, detail=str(ex)) from ex
    return results

# Return APPLICATION_TITLE
@app.get("/getApplicationTitle")
async def get_application_title():
    """Get the application title text
    
    Returns:
        dict: A dictionary containing the application title.
    """
    response = {
            "APPLICATION_TITLE": ENV["APPLICATION_TITLE"]
        }
    return response

@app.get("/getalltags")
async def get_all_tags():
    """
    Get the status of all tags in the system

    Returns:
        dict: A dictionary containing the status of all tags
    """
    try:
        results = statusLog.get_all_tags()
    except Exception as ex:
        log.exception("Exception in /getalltags")
        raise HTTPException(status_code=500, detail=str(ex)) from ex
    return results


@app.get("/getFeatureFlags")
async def get_feature_flags():
    """
    Get the feature flag settings for the app.

    Returns:
        dict: A dictionary containing various feature flags for the app.
            - "ENABLE_WEB_CHAT": Flag indicating whether web chat is enabled.
            - "ENABLE_UNGROUNDED_CHAT": Flag indicating whether ungrounded chat is enabled.
            - "ENABLE_MATH_ASSISTANT": Flag indicating whether the math assistant is enabled.
            - "ENABLE_TABULAR_DATA_ASSISTANT": Flag indicating whether the tabular data assistant is enabled.
    """
    response = {
        "ENABLE_WEB_CHAT": str_to_bool.get(ENV["ENABLE_WEB_CHAT"]),
        "ENABLE_UNGROUNDED_CHAT": str_to_bool.get(ENV["ENABLE_UNGROUNDED_CHAT"]),
        "ENABLE_MATH_ASSISTANT": str_to_bool.get(ENV["ENABLE_MATH_ASSISTANT"]),
        "ENABLE_TABULAR_DATA_ASSISTANT": str_to_bool.get(ENV["ENABLE_TABULAR_DATA_ASSISTANT"]),
    }
    return response

@app.post("/file")  
async def upload_file(  
    file: UploadFile = File(...),   
    file_path: str = Form(...),
    tags: str = Form(None)  
):  
    """  
    Upload a file to Azure Blob Storage.  
    Parameters:  
    - file: The file to upload.
    - file_path: The path to save the file in Blob Storage.
    - tags: The tags to associate with the file.  
    Returns:  
    - response: A message indicating the result of the upload.  
    """  
    try:          
        blob_upload_client = blob_upload_container_client.get_blob_client(file_path)  
  
        blob_upload_client.upload_blob(
            file.file,
            overwrite=True,
            content_settings=ContentSettings(content_type=file.content_type),
            metadata= {"tags": tags}
        )
  
        return {"message": f"File '{file.filename}' uploaded successfully"}  
  
    except Exception as ex:  
        log.exception("Exception in /file")  
        raise HTTPException(status_code=500, detail=str(ex)) from ex  

@app.post("/get-file")
async def get_file(request: Request):
    data = await request.json()
    file_path = data['path']

    # Extract container name and blob name from the file path
    container_name, blob_name = file_path.split('/', 1)

    # Download the blob to a local file
    
    citation_blob_client = blob_upload_container_client.get_blob_client(blob=blob_name)
    stream = citation_blob_client.download_blob().chunks()
    blob_properties = citation_blob_client.get_blob_properties()

    return StreamingResponse(stream,
                             media_type=blob_properties.content_settings.content_type, 
                             headers={"Content-Disposition": f"inline; filename={blob_name}"})

#app.mount("/", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    log.info("IA WebApp Starting Up...")
