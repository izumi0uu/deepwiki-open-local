import os
import logging
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from typing import List, Optional, Dict, Any, Literal
import json
from datetime import datetime
from pydantic import BaseModel, Field
import google.generativeai as genai
import asyncio

# Configure logging
from api.logging_config import setup_logging
from api.local_repo_filters import (
    build_local_browse_response,
    build_repo_filter,
    file_is_within_size_limit,
    filter_cache_suffix,
    get_allowed_local_repo_root_entries,
    is_binary_file,
    load_gitignore_rules,
    resolve_local_repo_path,
    should_descend_dir,
    should_include_path,
)

from api.storage import get_adalflow_default_root_path

setup_logging()
logger = logging.getLogger(__name__)


# Initialize FastAPI app
app = FastAPI(
    title="Streaming API",
    description="API for streaming chat completions"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# --- Pydantic Models ---
class WikiPage(BaseModel):
    """
    Model for a wiki page.
    """
    id: str
    title: str
    content: str
    filePaths: List[str]
    importance: str # Should ideally be Literal['high', 'medium', 'low']
    relatedPages: List[str]

class ProcessedProjectEntry(BaseModel):
    id: str  # Filename
    owner: str
    repo: str
    name: str  # owner/repo
    repo_type: str # Renamed from type to repo_type for clarity with existing models
    submittedAt: int # Timestamp
    language: str # Extracted from filename
    variant: Optional[str] = None
    comprehensive: Optional[bool] = None

class RepoInfo(BaseModel):
    owner: str
    repo: str
    type: str
    token: Optional[str] = None
    localPath: Optional[str] = None
    repoUrl: Optional[str] = None


class FileFilterInfo(BaseModel):
    excluded_dirs: Optional[str] = None
    excluded_files: Optional[str] = None
    included_dirs: Optional[str] = None
    included_files: Optional[str] = None


class WikiSection(BaseModel):
    """
    Model for the wiki sections.
    """
    id: str
    title: str
    pages: List[str]
    subsections: Optional[List[str]] = None


class WikiStructureModel(BaseModel):
    """
    Model for the overall wiki structure.
    """
    id: str
    title: str
    description: str
    pages: List[WikiPage]
    sections: Optional[List[WikiSection]] = None
    rootSections: Optional[List[str]] = None

class WikiCacheData(BaseModel):
    """
    Model for the data to be stored in the wiki cache.
    """
    wiki_structure: WikiStructureModel
    generated_pages: Dict[str, WikiPage]
    repo_url: Optional[str] = None  #compatible for old cache
    repo: Optional[RepoInfo] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    comprehensive: Optional[bool] = None

class WikiCacheRequest(BaseModel):
    """
    Model for the request body when saving wiki cache.
    """
    repo: RepoInfo
    language: str
    comprehensive: bool = True
    wiki_structure: WikiStructureModel
    generated_pages: Dict[str, WikiPage]
    provider: str
    model: str
    file_filters: Optional[FileFilterInfo] = None

class WikiExportRequest(BaseModel):
    """
    Model for requesting a wiki export.
    """
    repo_url: str = Field(..., description="URL of the repository")
    pages: List[WikiPage] = Field(..., description="List of wiki pages to export")
    format: Literal["markdown", "json"] = Field(..., description="Export format (markdown or json)")

# --- Model Configuration Models ---
class Model(BaseModel):
    """
    Model for LLM model configuration
    """
    id: str = Field(..., description="Model identifier")
    name: str = Field(..., description="Display name for the model")

class Provider(BaseModel):
    """
    Model for LLM provider configuration
    """
    id: str = Field(..., description="Provider identifier")
    name: str = Field(..., description="Display name for the provider")
    models: List[Model] = Field(..., description="List of available models for this provider")
    supportsCustomModel: Optional[bool] = Field(False, description="Whether this provider supports custom models")

class ModelConfig(BaseModel):
    """
    Model for the entire model configuration
    """
    providers: List[Provider] = Field(..., description="List of available model providers")
    defaultProvider: str = Field(..., description="ID of the default provider")

class AuthorizationConfig(BaseModel):
    code: str = Field(..., description="Authorization code")

from api.config import configs, WIKI_AUTH_MODE, WIKI_AUTH_CODE

@app.get("/lang/config")
async def get_lang_config():
    return configs["lang_config"]

@app.get("/auth/status")
async def get_auth_status():
    """
    Check if authentication is required for the wiki.
    """
    return {"auth_required": WIKI_AUTH_MODE}

@app.post("/auth/validate")
async def validate_auth_code(request: AuthorizationConfig):
    """
    Check authorization code.
    """
    return {"success": WIKI_AUTH_CODE == request.code}

@app.get("/models/config", response_model=ModelConfig)
async def get_model_config():
    """
    Get available model providers and their models.

    This endpoint returns the configuration of available model providers and their
    respective models that can be used throughout the application.

    Returns:
        ModelConfig: A configuration object containing providers and their models
    """
    try:
        logger.info("Fetching model configurations")

        # Create providers from the config file
        providers = []
        default_provider = configs.get("default_provider", "google")

        # Add provider configuration based on config.py
        for provider_id, provider_config in configs["providers"].items():
            models = []
            # Add models from config
            for model_id in provider_config["models"].keys():
                # Get a more user-friendly display name if possible
                models.append(Model(id=model_id, name=model_id))

            # Add provider with its models
            providers.append(
                Provider(
                    id=provider_id,
                    name=f"{provider_id.capitalize()}",
                    supportsCustomModel=provider_config.get("supportsCustomModel", False),
                    models=models
                )
            )

        # Create and return the full configuration
        config = ModelConfig(
            providers=providers,
            defaultProvider=default_provider
        )
        return config

    except Exception as e:
        logger.error(f"Error creating model configuration: {str(e)}")
        # Return some default configuration in case of error
        return ModelConfig(
            providers=[
                Provider(
                    id="google",
                    name="Google",
                    supportsCustomModel=True,
                    models=[
                        Model(id="gemini-2.5-flash", name="Gemini 2.5 Flash")
                    ]
                )
            ],
            defaultProvider="google"
        )

@app.post("/export/wiki")
async def export_wiki(request: WikiExportRequest):
    """
    Export wiki content as Markdown or JSON.

    Args:
        request: The export request containing wiki pages and format

    Returns:
        A downloadable file in the requested format
    """
    try:
        logger.info(f"Exporting wiki for {request.repo_url} in {request.format} format")

        # Extract repository name from URL for the filename
        repo_parts = request.repo_url.rstrip('/').split('/')
        repo_name = repo_parts[-1] if len(repo_parts) > 0 else "wiki"

        # Get current timestamp for the filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if request.format == "markdown":
            # Generate Markdown content
            content = generate_markdown_export(request.repo_url, request.pages)
            filename = f"{repo_name}_wiki_{timestamp}.md"
            media_type = "text/markdown"
        else:  # JSON format
            # Generate JSON content
            content = generate_json_export(request.repo_url, request.pages)
            filename = f"{repo_name}_wiki_{timestamp}.json"
            media_type = "application/json"

        # Create response with appropriate headers for file download
        response = Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )

        return response

    except Exception as e:
        error_msg = f"Error exporting wiki: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/local_repo/roots")
async def get_local_repo_roots():
    """Return local repository roots available to this DeepWiki instance."""
    return {"roots": get_allowed_local_repo_root_entries()}


@app.get("/local_repo/browse")
async def browse_local_repo(path: str = Query(..., description="Directory path to browse")):
    """Return immediate child directories for an allowed local path."""
    try:
        return build_local_browse_response(path)
    except FileNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Directory not found: {path}"}
        )
    except PermissionError as e:
        return JSONResponse(
            status_code=403,
            content={"error": str(e)}
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"error": str(e)}
        )


@app.get("/local_repo/structure")
async def get_local_repo_structure(
    path: str = Query(None, description="Path to local repository"),
    excluded_dirs: Optional[str] = Query(None, description="Newline or comma separated directory patterns to exclude"),
    excluded_files: Optional[str] = Query(None, description="Newline or comma separated file patterns to exclude"),
    included_dirs: Optional[str] = Query(None, description="Newline or comma separated directory patterns to include exclusively"),
    included_files: Optional[str] = Query(None, description="Newline or comma separated file patterns to include exclusively"),
):
    """Return the file tree and README content for a local repository."""
    if not path:
        return JSONResponse(
            status_code=400,
            content={"error": "No path provided. Please provide a 'path' query parameter."}
        )

    try:
        resolved_path = resolve_local_repo_path(path)
    except FileNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Directory not found: {path}"}
        )
    except PermissionError as e:
        return JSONResponse(
            status_code=403,
            content={"error": str(e)}
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"error": str(e)}
        )

    try:
        logger.info(f"Processing local repository at: {resolved_path}")
        repo_filter = build_repo_filter(
            excluded_dirs=excluded_dirs,
            excluded_files=excluded_files,
            included_dirs=included_dirs,
            included_files=included_files,
        )
        gitignore_rules = load_gitignore_rules(resolved_path)
        file_tree_lines = []
        readme_content = ""

        for root, dirs, files in os.walk(resolved_path):
            rel_dir = os.path.relpath(root, resolved_path)
            dirs[:] = [
                directory
                for directory in dirs
                if should_descend_dir(
                    os.path.join(rel_dir, directory) if rel_dir != "." else directory,
                    repo_filter,
                    gitignore_rules,
                )
            ]

            for file in files:
                rel_file = os.path.join(rel_dir, file) if rel_dir != "." else file
                full_path = os.path.join(root, file)

                if not should_include_path(rel_file, repo_filter, gitignore_rules):
                    continue
                if not file_is_within_size_limit(full_path) or is_binary_file(full_path):
                    continue

                file_tree_lines.append(rel_file)
                if file.lower() in {"readme.md", "readme.rst", "readme.txt"} and not readme_content:
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            readme_content = f.read()
                    except Exception as e:
                        logger.warning(f"Could not read README file {rel_file}: {str(e)}")
                        readme_content = ""

        file_tree_str = '\n'.join(sorted(file_tree_lines))
        return {"file_tree": file_tree_str, "readme": readme_content, "root": resolved_path}
    except Exception as e:
        logger.error(f"Error processing local repository: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Error processing local repository: {str(e)}"}
        )

def generate_markdown_export(repo_url: str, pages: List[WikiPage]) -> str:
    """
    Generate Markdown export of wiki pages.

    Args:
        repo_url: The repository URL
        pages: List of wiki pages

    Returns:
        Markdown content as string
    """
    # Start with metadata
    markdown = f"# Wiki Documentation for {repo_url}\n\n"
    markdown += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    # Add table of contents
    markdown += "## Table of Contents\n\n"
    for page in pages:
        markdown += f"- [{page.title}](#{page.id})\n"
    markdown += "\n"

    # Add each page
    for page in pages:
        markdown += f"<a id='{page.id}'></a>\n\n"
        markdown += f"## {page.title}\n\n"



        # Add related pages
        if page.relatedPages and len(page.relatedPages) > 0:
            markdown += "### Related Pages\n\n"
            related_titles = []
            for related_id in page.relatedPages:
                # Find the title of the related page
                related_page = next((p for p in pages if p.id == related_id), None)
                if related_page:
                    related_titles.append(f"[{related_page.title}](#{related_id})")

            if related_titles:
                markdown += "Related topics: " + ", ".join(related_titles) + "\n\n"

        # Add page content
        markdown += f"{page.content}\n\n"
        markdown += "---\n\n"

    return markdown

def generate_json_export(repo_url: str, pages: List[WikiPage]) -> str:
    """
    Generate JSON export of wiki pages.

    Args:
        repo_url: The repository URL
        pages: List of wiki pages

    Returns:
        JSON content as string
    """
    # Create a dictionary with metadata and pages
    export_data = {
        "metadata": {
            "repository": repo_url,
            "generated_at": datetime.now().isoformat(),
            "page_count": len(pages)
        },
        "pages": [page.model_dump() for page in pages]
    }

    # Convert to JSON string with pretty formatting
    return json.dumps(export_data, indent=2)

# Import the simplified chat implementation
from api.simple_chat import chat_completions_stream
from api.websocket_wiki import handle_websocket_chat

# Add the chat_completions_stream endpoint to the main app
app.add_api_route("/chat/completions/stream", chat_completions_stream, methods=["POST"])

# Add the WebSocket endpoint
app.add_api_websocket_route("/ws/chat", handle_websocket_chat)

# --- Wiki Cache Helper Functions ---

WIKI_CACHE_DIR = os.path.join(get_adalflow_default_root_path(), "wikicache")
os.makedirs(WIKI_CACHE_DIR, exist_ok=True)

def _cache_variant_suffix(
    excluded_dirs: Optional[str] = None,
    excluded_files: Optional[str] = None,
    included_dirs: Optional[str] = None,
    included_files: Optional[str] = None,
    comprehensive: Optional[bool] = None,
) -> str:
    suffix = filter_cache_suffix(
        excluded_dirs=excluded_dirs,
        excluded_files=excluded_files,
        included_dirs=included_dirs,
        included_files=included_files,
    )
    parts = []
    if comprehensive is not None:
        parts.append("comprehensive" if comprehensive else "concise")
    if suffix:
        parts.append(suffix)
    return f"_{'_'.join(parts)}" if parts else ""


def get_wiki_cache_path(
    owner: str,
    repo: str,
    repo_type: str,
    language: str,
    excluded_dirs: Optional[str] = None,
    excluded_files: Optional[str] = None,
    included_dirs: Optional[str] = None,
    included_files: Optional[str] = None,
    comprehensive: Optional[bool] = None,
) -> str:
    """Generates the file path for a given wiki cache."""
    variant = _cache_variant_suffix(excluded_dirs, excluded_files, included_dirs, included_files, comprehensive)
    filename = f"deepwiki_cache_{repo_type}_{owner}_{repo}_{language}{variant}.json"
    return os.path.join(WIKI_CACHE_DIR, filename)


def _resolve_cache_id_path(cache_id: str) -> str:
    if not cache_id or os.path.basename(cache_id) != cache_id:
        raise ValueError("Invalid cache id")
    if not cache_id.startswith("deepwiki_cache_") or not cache_id.endswith(".json"):
        raise ValueError("Invalid cache id")
    cache_path = os.path.realpath(os.path.join(WIKI_CACHE_DIR, cache_id))
    if os.path.commonpath([cache_path, os.path.realpath(WIKI_CACHE_DIR)]) != os.path.realpath(WIKI_CACHE_DIR):
        raise ValueError("Invalid cache id")
    return cache_path


def _parse_wiki_cache_filename(filename: str) -> Optional[Dict[str, Any]]:
    if not filename.startswith("deepwiki_cache_") or not filename.endswith(".json"):
        return None

    stem = filename[len("deepwiki_cache_"):-len(".json")]
    parts = stem.split("_")
    if len(parts) < 4:
        return None

    supported_languages = set(configs.get("lang_config", {}).get("supported_languages", []))
    language_index = None
    for index in range(len(parts) - 1, 1, -1):
        if parts[index] in supported_languages:
            language_index = index
            break
    if language_index is None:
        language_index = len(parts) - 1

    if language_index <= 2:
        return None

    variant_parts = parts[language_index + 1:]
    comprehensive = None
    if "comprehensive" in variant_parts:
        comprehensive = True
    elif "concise" in variant_parts:
        comprehensive = False

    return {
        "repo_type": parts[0],
        "owner": parts[1],
        "repo": "_".join(parts[2:language_index]),
        "language": parts[language_index],
        "variant": "_".join(variant_parts) or None,
        "comprehensive": comprehensive,
    }


def read_wiki_cache_file(cache_path: str) -> Optional[WikiCacheData]:
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return WikiCacheData(**data)
    except Exception as e:
        logger.error(f"Error reading wiki cache from {cache_path}: {e}")
        return None

async def read_wiki_cache(
    owner: str,
    repo: str,
    repo_type: str,
    language: str,
    excluded_dirs: Optional[str] = None,
    excluded_files: Optional[str] = None,
    included_dirs: Optional[str] = None,
    included_files: Optional[str] = None,
    comprehensive: Optional[bool] = None,
) -> Optional[WikiCacheData]:
    """Reads wiki cache data from the file system."""
    cache_path = get_wiki_cache_path(owner, repo, repo_type, language, excluded_dirs, excluded_files, included_dirs, included_files, comprehensive)
    if os.path.exists(cache_path):
        return await asyncio.to_thread(read_wiki_cache_file, cache_path)
    return None

async def save_wiki_cache(data: WikiCacheRequest) -> bool:
    """Saves wiki cache data to the file system."""
    filters = data.file_filters or FileFilterInfo()
    cache_path = get_wiki_cache_path(
        data.repo.owner,
        data.repo.repo,
        data.repo.type,
        data.language,
        filters.excluded_dirs,
        filters.excluded_files,
        filters.included_dirs,
        filters.included_files,
        data.comprehensive,
    )
    logger.info(f"Attempting to save wiki cache. Path: {cache_path}")
    try:
        payload = WikiCacheData(
            wiki_structure=data.wiki_structure,
            generated_pages=data.generated_pages,
            repo=data.repo,
            provider=data.provider,
            model=data.model,
            comprehensive=data.comprehensive,
        )
        # Log size of data to be cached for debugging (avoid logging full content if large)
        try:
            payload_json = payload.model_dump_json()
            payload_size = len(payload_json.encode('utf-8'))
            logger.info(f"Payload prepared for caching. Size: {payload_size} bytes.")
        except Exception as ser_e:
            logger.warning(f"Could not serialize payload for size logging: {ser_e}")


        logger.info(f"Writing cache file to: {cache_path}")
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(payload.model_dump(), f, indent=2)
        logger.info(f"Wiki cache successfully saved to {cache_path}")
        return True
    except IOError as e:
        logger.error(f"IOError saving wiki cache to {cache_path}: {e.strerror} (errno: {e.errno})", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error saving wiki cache to {cache_path}: {e}", exc_info=True)
        return False

# --- Wiki Cache API Endpoints ---

@app.get("/api/wiki_cache", response_model=Optional[WikiCacheData])
async def get_cached_wiki(
    owner: str = Query(..., description="Repository owner"),
    repo: str = Query(..., description="Repository name"),
    repo_type: str = Query(..., description="Repository type (e.g., github, gitlab)"),
    language: str = Query(..., description="Language of the wiki content"),
    excluded_dirs: Optional[str] = Query(None, description="Directory filters used to build the wiki"),
    excluded_files: Optional[str] = Query(None, description="File filters used to build the wiki"),
    included_dirs: Optional[str] = Query(None, description="Included directory filters used to build the wiki"),
    included_files: Optional[str] = Query(None, description="Included file filters used to build the wiki"),
    comprehensive: Optional[bool] = Query(None, description="Whether the wiki was generated in comprehensive mode")
):
    """
    Retrieves cached wiki data (structure and generated pages) for a repository.
    """
    # Language validation
    supported_langs = configs["lang_config"]["supported_languages"]
    if not supported_langs.__contains__(language):
        language = configs["lang_config"]["default"]

    logger.info(f"Attempting to retrieve wiki cache for {owner}/{repo} ({repo_type}), lang: {language}")
    cached_data = await read_wiki_cache(owner, repo, repo_type, language, excluded_dirs, excluded_files, included_dirs, included_files, comprehensive)
    if cached_data:
        return cached_data
    else:
        # Return 200 with null body if not found, as frontend expects this behavior
        # Or, raise HTTPException(status_code=404, detail="Wiki cache not found") if preferred
        logger.info(f"Wiki cache not found for {owner}/{repo} ({repo_type}), lang: {language}")
        return None

@app.post("/api/wiki_cache")
async def store_wiki_cache(request_data: WikiCacheRequest):
    """
    Stores generated wiki data (structure and pages) to the server-side cache.
    """
    # Language validation
    supported_langs = configs["lang_config"]["supported_languages"]

    if not supported_langs.__contains__(request_data.language):
        request_data.language = configs["lang_config"]["default"]

    logger.info(f"Attempting to save wiki cache for {request_data.repo.owner}/{request_data.repo.repo} ({request_data.repo.type}), lang: {request_data.language}")
    success = await save_wiki_cache(request_data)
    if success:
        return {"message": "Wiki cache saved successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save wiki cache")

@app.delete("/api/wiki_cache")
async def delete_wiki_cache(
    owner: str = Query(..., description="Repository owner"),
    repo: str = Query(..., description="Repository name"),
    repo_type: str = Query(..., description="Repository type (e.g., github, gitlab)"),
    language: str = Query(..., description="Language of the wiki content"),
    excluded_dirs: Optional[str] = Query(None, description="Directory filters used to build the wiki"),
    excluded_files: Optional[str] = Query(None, description="File filters used to build the wiki"),
    included_dirs: Optional[str] = Query(None, description="Included directory filters used to build the wiki"),
    included_files: Optional[str] = Query(None, description="Included file filters used to build the wiki"),
    comprehensive: Optional[bool] = Query(None, description="Whether the wiki was generated in comprehensive mode"),
    authorization_code: Optional[str] = Query(None, description="Authorization code")
):
    """
    Deletes a specific wiki cache from the file system.
    """
    # Language validation
    supported_langs = configs["lang_config"]["supported_languages"]
    if not supported_langs.__contains__(language):
        raise HTTPException(status_code=400, detail="Language is not supported")

    if WIKI_AUTH_MODE:
        logger.info("check the authorization code")
        if not authorization_code or WIKI_AUTH_CODE != authorization_code:
            raise HTTPException(status_code=401, detail="Authorization code is invalid")

    logger.info(f"Attempting to delete wiki cache for {owner}/{repo} ({repo_type}), lang: {language}")
    cache_path = get_wiki_cache_path(owner, repo, repo_type, language, excluded_dirs, excluded_files, included_dirs, included_files, comprehensive)

    if os.path.exists(cache_path):
        try:
            os.remove(cache_path)
            logger.info(f"Successfully deleted wiki cache: {cache_path}")
            return {"message": f"Wiki cache for {owner}/{repo} ({language}) deleted successfully"}
        except Exception as e:
            logger.error(f"Error deleting wiki cache {cache_path}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to delete wiki cache: {str(e)}")
    else:
        logger.warning(f"Wiki cache not found, cannot delete: {cache_path}")
        raise HTTPException(status_code=404, detail="Wiki cache not found")

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker and monitoring"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "deepwiki-api"
    }

@app.get("/")
async def root():
    """Root endpoint to check if the API is running and list available endpoints dynamically."""
    # Collect routes dynamically from the FastAPI app
    endpoints = {}
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            # Skip docs and static routes
            if route.path in ["/openapi.json", "/docs", "/redoc", "/favicon.ico"]:
                continue
            # Group endpoints by first path segment
            path_parts = route.path.strip("/").split("/")
            group = path_parts[0].capitalize() if path_parts[0] else "Root"
            method_list = list(route.methods - {"HEAD", "OPTIONS"})
            for method in method_list:
                endpoints.setdefault(group, []).append(f"{method} {route.path}")

    # Optionally, sort endpoints for readability
    for group in endpoints:
        endpoints[group].sort()

    return {
        "message": "Welcome to Streaming API",
        "version": "1.0.0",
        "endpoints": endpoints
    }

# --- Processed Projects Endpoint --- (New Endpoint)
@app.get("/api/processed_projects", response_model=List[ProcessedProjectEntry])
async def get_processed_projects():
    """
    Lists all processed projects found in the wiki cache directory.
    Projects are identified by files named like: deepwiki_cache_{repo_type}_{owner}_{repo}_{language}.json
    """
    project_entries: List[ProcessedProjectEntry] = []
    # WIKI_CACHE_DIR is already defined globally in the file

    try:
        if not os.path.exists(WIKI_CACHE_DIR):
            logger.info(f"Cache directory {WIKI_CACHE_DIR} not found. Returning empty list.")
            return []

        logger.info(f"Scanning for project cache files in: {WIKI_CACHE_DIR}")
        filenames = await asyncio.to_thread(os.listdir, WIKI_CACHE_DIR) # Use asyncio.to_thread for os.listdir

        for filename in filenames:
            parsed = _parse_wiki_cache_filename(filename)
            if not parsed:
                continue

            file_path = os.path.join(WIKI_CACHE_DIR, filename)
            try:
                stats = await asyncio.to_thread(os.stat, file_path) # Use asyncio.to_thread for os.stat
                owner = parsed["owner"]
                repo = parsed["repo"]

                project_entries.append(
                    ProcessedProjectEntry(
                        id=filename,
                        owner=owner,
                        repo=repo,
                        name=f"{owner}/{repo}",
                        repo_type=parsed["repo_type"],
                        submittedAt=int(stats.st_mtime * 1000), # Convert to milliseconds
                        language=parsed["language"],
                        variant=parsed["variant"],
                        comprehensive=parsed["comprehensive"],
                    )
                )
            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")
                continue # Skip this file on error

        # Sort by most recent first
        project_entries.sort(key=lambda p: p.submittedAt, reverse=True)
        logger.info(f"Found {len(project_entries)} processed project entries.")
        return project_entries

    except Exception as e:
        logger.error(f"Error listing processed projects from {WIKI_CACHE_DIR}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list processed projects from server cache.")
