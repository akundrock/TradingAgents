# TradingAgents: LLM Provider Architecture

## Overview

TradingAgents supports multiple LLM providers through a factory-based abstraction pattern. Agents can dynamically switch between providers (OpenAI, Anthropic, Google Gemini, Azure, Bedrock) without code changes—just configuration.

## Multi-Provider Architecture

### Factory Pattern

**Location**: `tradingagents/llm_clients/factory.py`

All LLM client creation goes through a single factory:

```python
def create_llm_client(
    provider: str,           # "openai", "anthropic", "google", "azure", "bedrock"
    model: str,              # "gpt-5.5", "claude-opus-4", "gemini-2.0", etc.
    base_url: str | None = None,  # Custom endpoint (e.g., local inference server)
    temperature: float = 0.7,
    **kwargs,
) → BaseLLMClient:
    """Factory returns appropriate client implementation."""
    
    if provider == "openai":
        return OpenAIClient(model=model, base_url=base_url, **kwargs)
    elif provider == "anthropic":
        return AnthropicClient(model=model, **kwargs)
    elif provider == "google":
        return GoogleClient(model=model, **kwargs)
    elif provider == "azure":
        return AzureClient(model=model, base_url=base_url, **kwargs)
    elif provider == "bedrock":
        return BedrockClient(model=model, **kwargs)
    else:
        raise ValueError(f"Unknown provider: {provider}")
```

### Base Client Interface

**Location**: `tradingagents/llm_clients/base_client.py`

```python
class BaseLLMClient(ABC):
    """Abstract interface all providers implement."""
    
    @abstractmethod
    def get_llm(self) → Any:
        """Return LangChain LLM instance ready for use."""
        pass
    
    @abstractmethod
    def validate_model(self) → bool:
        """Check if model is in known list for this provider."""
        pass
    
    def warn_if_unknown_model(self) → None:
        """Emit warning if model is not recognized."""
        pass
    
    def get_provider_name(self) → str:
        """Return provider name for logging/warnings."""
        pass
```

### Provider Implementations

#### 1. OpenAI Client

**Location**: `tradingagents/llm_clients/openai_client.py`

**Supported Models**:
- GPT-5.5, GPT-5.4, GPT-5.x family
- o1-preview, o1-mini (reasoning models)
- o3-mini, o3 (advanced reasoning)
- gpt-4o, gpt-4-turbo, gpt-4 (legacy)

**Configuration**:
```python
client = create_llm_client(
    provider="openai",
    model="gpt-5.5",
    base_url=None,  # Uses https://api.openai.com/v1
    temperature=0.7,
)
```

**Environment Variables**:
```bash
OPENAI_API_KEY=<your-key>                           # Required
TRADINGAGENTS_OPENAI_REASONING_EFFORT=<effort>      # Optional: low/medium/high
```

**Structured Output**:
- Uses `json_schema` mode (strict schema enforcement)
- Pydantic models converted to JSON Schema
- Reliable structured output parsing

**Reasoning Models**:
- `o1-preview`, `o1-mini`: Long-form reasoning with token cost tradeoff
- `o3-mini`, `o3`: Advanced reasoning with configurable thinking levels

**Key Features**:
- Lowest cost among reasoning models (for volume)
- Fastest inference for non-reasoning models
- Best structured output support
- Native vision capabilities (if needed)

#### 2. Anthropic Client

**Location**: `tradingagents/llm_clients/anthropic_client.py`

**Supported Models**:
- Claude 4.x (Opus, Sonnet, Haiku variants)
- Claude 3.x family
- Claude 2.x (legacy)

**Configuration**:
```python
client = create_llm_client(
    provider="anthropic",
    model="claude-opus-4",
    base_url=None,
    temperature=0.7,
)
```

**Environment Variables**:
```bash
ANTHROPIC_API_KEY=<your-key>                    # Required
TRADINGAGENTS_ANTHROPIC_EFFORT=<effort>         # Optional: low/medium/high (for extended thinking)
```

**Structured Output**:
- Uses `tool_use` mode (Anthropic's native tool-calling)
- Define schema as tool, force `tool_choice`
- Handles both XML and text extraction

**Extended Thinking**:
- Configurable thinking budget (budget_tokens)
- Trade compute for reasoning quality

**Key Features**:
- Excellent instruction-following and reasoning
- Strong context window handling
- Native streaming support for real-time updates
- Constitutional AI safety alignment

#### 3. Google Gemini Client

**Location**: `tradingagents/llm_clients/google_client.py`

**Supported Models**:
- Gemini 2.0 (exp and standard variants)
- Gemini 1.5 Pro, Flash variants
- Gemini 1.0 (legacy)

**Configuration**:
```python
client = create_llm_client(
    provider="google",
    model="gemini-2.0",
    base_url=None,
    temperature=0.7,
)
```

**Environment Variables**:
```bash
GOOGLE_API_KEY=<your-key>                       # Required
TRADINGAGENTS_GOOGLE_THINKING_LEVEL=<level>     # Optional: off/low/medium/high
```

**Structured Output**:
- Uses native `response_schema` mode
- Pydantic models converted to Gemini schema
- Strong schema compliance

**Thinking Mode**:
- Configurable thinking level (off, low, medium, high)
- Budget tokens automatically managed

**Key Features**:
- Excellent multimodal support (images, audio, video)
- Large context window (1M tokens for 2.0)
- Fast inference
- Good cost-to-performance ratio

#### 4. Azure OpenAI Client

**Location**: `tradingagents/llm_clients/azure_client.py`

**Configuration**:
```python
client = create_llm_client(
    provider="azure",
    model="gpt-5.5",
    base_url="https://<your-instance>.openai.azure.com/",
)
```

**Environment Variables**:
```bash
AZURE_OPENAI_API_KEY=<your-key>                 # Required
AZURE_OPENAI_ENDPOINT=https://<instance>.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-12-01             # Optional
```

**Features**:
- Same models as OpenAI but hosted on Azure infrastructure
- Enterprise VNet support
- SOC2/HIPAA compliance options
- Regional deployment control

#### 5. AWS Bedrock Client

**Location**: `tradingagents/llm_clients/bedrock_client.py`

**Supported Models**:
- Claude models (via Bedrock)
- Llama, Mistral models
- Amazon Titan models

**Configuration**:
```python
client = create_llm_client(
    provider="bedrock",
    model="claude-opus-4",
    region_name="us-east-1",
)
```

**Environment Variables**:
```bash
AWS_ACCESS_KEY_ID=<your-key>        # Required
AWS_SECRET_ACCESS_KEY=<your-secret> # Required
AWS_REGION=us-east-1                # Optional
BEDROCK_REGION=us-east-1            # Optional (overrides AWS_REGION)
```

**Installation**:
```bash
pip install "tradingagents[bedrock]"  # Installs langchain-aws
```

**Features**:
- AWS SigV4 authentication (no API key exposure)
- On-demand and provisioned throughput models
- VPC endpoint support
- Batch processing integration

## Model Catalog and Validation

**Location**: `tradingagents/llm_clients/model_catalog.py`

The system maintains a catalog of known models for each provider:

```python
MODEL_CATALOG = {
    "openai": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-4o",
        "o1-preview",
        "o3-mini",
        ...
    ],
    "anthropic": [
        "claude-opus-4",
        "claude-sonnet-4",
        "claude-haiku-3",
        ...
    ],
    "google": [
        "gemini-2.0-exp",
        "gemini-2.0",
        "gemini-1.5-pro",
        ...
    ],
    ...
}
```

**Usage**:
```python
client = create_llm_client(provider="openai", model="gpt-5.5")
if not client.validate_model():
    client.warn_if_unknown_model()  # Warn but continue
```

## Structured Output Support

TradingAgents uses structured output (Pydantic models) for three decision-making agents:
1. **Research Manager** → `ResearchPlan` schema
2. **Trader** → `TraderProposal` schema
3. **Portfolio Manager** → `PortfolioDecision` schema

Each provider implements structured output differently:

### Provider-Specific Modes

#### OpenAI: `json_schema`
```python
# Pydantic model automatically converted to JSON Schema
response = llm.with_structured_output(ResearchPlan, method="json_schema")
plan = response.invoke(prompt)  # Returns parsed ResearchPlan instance
```

#### Anthropic: `tool_use`
```python
# Schema converted to tool definition
# Model chooses tool_use block; tool invocation extracted
response = llm.bind_tools([ResearchPlan], tool_choice="any")
plan = response.invoke(prompt)  # Parses tool arguments
```

#### Google: `response_schema`
```python
# Pydantic converted to Gemini schema format
response = llm.with_structured_output(ResearchPlan, method="google_native")
plan = response.invoke(prompt)  # Returns parsed ResearchPlan
```

#### Custom Endpoints (OpenAI-compatible)
```python
client = create_llm_client(
    provider="openai",
    model="local-model",
    base_url="http://localhost:8000/v1",  # Point to local server
)
# Uses same json_schema mode as OpenAI
```

### Schema Enforcement Strategy

Field descriptions in Pydantic models become the model's instructions:

```python
class ResearchPlan(BaseModel):
    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where "
            "evidence is genuinely balanced."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides, "
            "ending with which arguments led to the recommendation."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader, including position sizing "
            "consistent with the rating."
        ),
    )
```

The prompt body focuses on **context and reasoning guidance**, not schema details—schema field descriptions are the actual instructions.

## Thinking and Reasoning Models

Some providers offer native "thinking" or "reasoning" modes that allow the model to spend more tokens on internal reasoning before producing output.

### OpenAI o1/o3 Series
```bash
TRADINGAGENTS_OPENAI_REASONING_EFFORT=medium  # low/medium/high
```

Configuration scales the reasoning effort and associated token cost.

### Google Gemini Thinking Mode
```bash
TRADINGAGENTS_GOOGLE_THINKING_LEVEL=medium    # off/low/medium/high
```

Thinking mode helps with complex problems but adds latency and cost.

### Anthropic Extended Thinking
```bash
TRADINGAGENTS_ANTHROPIC_EFFORT=medium         # low/medium/high
```

Configurable thinking budget for deeper analysis.

## Custom Endpoints (OpenAI-Compatible)

Support for self-hosted or alternative LLM servers:

```python
client = create_llm_client(
    provider="openai",  # Use OpenAI client adapter
    model="whatever-model-name",
    base_url="http://localhost:8000/v1",  # Point to your server
)
```

**Supported servers**:
- Ollama (local)
- vLLM (inference server)
- Text Generation WebUI
- OpenRouter API
- Custom OpenAI-compatible endpoints

**Environment Variable**:
```bash
TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:8000/v1
```

## Configuration and Provider Selection

### Default Configuration

**Location**: `tradingagents/default_config.py`

```python
DEFAULT_CONFIG = {
    "llm_provider": "openai",              # Default provider
    "deep_think_llm": "gpt-5.5",           # For complex reasoning
    "quick_think_llm": "gpt-4o",           # For fast tasks
    "temperature": 0.7,                    # Sampling randomness
    "llm_max_retries": 3,                  # Retry budget for failures
    # Provider-specific thinking
    "openai_reasoning_effort": None,       # or "low"/"medium"/"high"
    "google_thinking_level": None,         # or "low"/"medium"/"high"
    "anthropic_effort": None,              # or "low"/"medium"/"high"
}
```

### Configuration Cascade

User choices override defaults in this order:

1. **Code defaults** (above)
2. **Environment variables**:
   ```bash
   TRADINGAGENTS_LLM_PROVIDER=anthropic
   TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-4
   TRADINGAGENTS_ANTHROPIC_EFFORT=high
   ```
3. **CLI prompts** (interactive selection)
4. **Hard-coded overrides** in your script:
   ```python
   config = DEFAULT_CONFIG.copy()
   config["llm_provider"] = "bedrock"
   ta = TradingAgentsGraph(config=config)
   ```

### CLI Provider Selection

The CLI interactively prompts for provider if not set via env var:

```
Select LLM Provider:
 1. OpenAI (GPT-5.x, o1, o3)
 2. Anthropic (Claude 4.x)
 3. Google Gemini (2.0, 1.5)
 4. Azure OpenAI
 5. AWS Bedrock
 6. OpenAI-Compatible Endpoint
```

Then prompts for model selection based on provider, and provider-specific options (reasoning effort, thinking level).

## Error Handling and Validation

### Missing API Keys

If a provider is selected but API key is missing:

```python
def ensure_api_key(provider: str) → None:
    """Raises ValueError if required API key not found."""
    required_vars = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
        "bedrock": "AWS_ACCESS_KEY_ID",  # or via boto3 config
    }
    var = required_vars[provider]
    if not os.getenv(var):
        raise ValueError(f"{var} not found; cannot use {provider}")
```

### Unknown Models

If a model is not in the catalog for a provider:

```python
client.warn_if_unknown_model()
# RuntimeWarning: Model 'custom-model' is not in the known model list...
# Continues anyway (model might work, might be new/unreleased)
```

### Model Validation Rules

Some providers have model-specific rules:

```python
# Example: o1/o3 models don't support temperature tuning
if model in ("o1", "o1-preview", "o3-mini", "o3"):
    if temperature != 1.0:
        warn("o1/o3 models ignore temperature; using 1.0")
```

## Response Content Normalization

Some providers (Google Gemini 3, OpenAI Responses API) return content as structured blocks:

```python
response.content = [
    {"type": "reasoning", "reasoning": "..."},
    {"type": "text", "text": "..."}
]
```

A normalization helper extracts only the text blocks:

```python
def normalize_content(response):
    """Extract text blocks from structured response."""
    if isinstance(response.content, list):
        texts = [
            item.get("text", "")
            for item in response.content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        response.content = "\n".join(texts)
    return response
```

## Usage in Agents

Agents receive LLM instances and use them like any LangChain LLM:

```python
# In trading_graph.py initialization
deep_client = create_llm_client(
    provider=config["llm_provider"],
    model=config["deep_think_llm"],
)
self.deep_thinking_llm = deep_client.get_llm()

# In agent code
response = self.deep_thinking_llm.invoke(prompt_text)
# response.content = agent's response
```

For structured output agents:

```python
# Bind structured output to LLM
structured_llm = self.deep_thinking_llm.with_structured_output(
    ResearchPlan,  # Pydantic model
    method="auto",  # Provider auto-detects best mode
)

plan = structured_llm.invoke(prompt)  # Returns ResearchPlan instance
```

## Testing Multiple Providers

The test suite includes provider-specific tests:

- `test_openai_compatible_provider.py` — Custom endpoint support
- `test_bedrock_provider.py` — AWS Bedrock setup
- `test_openai_reasoning_effort.py` — o1/o3 reasoning configuration
- `test_google_thinking_level.py` — Gemini thinking mode
- `test_anthropic_effort.py` — Claude extended thinking

Run tests for your provider:
```bash
pytest tests/test_bedrock_provider.py -v
```

## Best Practices for Provider Selection

1. **For complex reasoning** → Use o1/o3 (OpenAI), Claude Opus 4 (Anthropic), or Gemini 2.0 with thinking
2. **For cost** → Use GPT-4o (OpenAI) or Claude Sonnet 4 (Anthropic)
3. **For speed** → Use GPT-4o mini or Claude Haiku (fastest)
4. **For multimodal** → Use Gemini 2.0 or GPT-4o
5. **For enterprise** → Use Azure OpenAI or Bedrock
6. **For self-hosted** → Use Ollama or vLLM with OpenAI-compatible endpoint

## Customization for Your Modifications

### Using Bedrock for Magpie Integration
If your Magpie strategy uses ML models, consider offloading reasoning to Bedrock:

```python
config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "bedrock"
config["deep_think_llm"] = "claude-opus-4"
ta = TradingAgentsGraph(config=config)
```

No code changes needed; all agents automatically use Bedrock.

### Custom Endpoint for Specialized Models
If you have a custom model optimized for trading, use OpenAI-compatible mode:

```bash
TRADINGAGENTS_LLM_BACKEND_URL=http://your-server:8000/v1
TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_DEEP_THINK_LLM=your-model-name
```

---

**See Also**:
- [ARCHITECTURE.md](ARCHITECTURE.md) — System overview
- [CONFIGURATION.md](CONFIGURATION.md) — Configuration system
- [AGENTS.md](AGENTS.md) — How agents use LLMs
