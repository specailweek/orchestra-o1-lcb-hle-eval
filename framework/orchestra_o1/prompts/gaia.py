"""GAIA MainAgent prompt; keeps Orchestra-o1's trained delegation schema."""
from orchestra_o1.prompts.omnigaia import OmniGAIAMainAgentPrompt


class GAIAMainAgentPrompt(OmniGAIAMainAgentPrompt):
    """GAIA specialization of the existing Orchestra-o1 orchestration prompt."""

