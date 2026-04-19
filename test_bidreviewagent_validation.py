#!/usr/bin/env python
"""BidReviewAgent validation script - run BidReviewAgent and check logs."""

import asyncio
import sys
import os
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.agent.bid_review_agent import BidReviewAgent
from backend.config import get_settings

# Configure logging to file
LOG_DIR = Path(__file__).parent / "workspace" / "2b4bafee-345f-4d53-890a-210794d49adc" / "0605b6e2-7a54-466d-9c8a-d0982381fb4f" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "bidreviewagent_test.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Test data paths
TENDER_DOC_PATH = "/home/openclaw/bjt_agent/workspace/2b4bafee-345f-4d53-890a-210794d49adc/0605b6e2-7a54-466d-9c8a-d0982381fb4f/tender/招标文件_20260418092835_parsed.md"
BID_DOC_PATH = "/home/openclaw/bjt_agent/workspace/2b4bafee-345f-4d53-890a-210794d49adc/0605b6e2-7a54-466d-9c8a-d0982381fb4f/bid/投标文件_20260418092838_parsed.md"
RULE_DOC_PATH = "/home/openclaw/bjt_agent/docs/rules/001 资格项检查.md"

USER_ID = "2b4bafee-345f-4d53-890a-210794d49adc"
PROJECT_ID = "0605b6e2-7a54-466d-9c8a-d0982381fb4f"


async def main():
    logger.info("=" * 60)
    logger.info("Starting BidReviewAgent Validation Test")
    logger.info("=" * 60)

    # Verify paths exist
    for path_str, path_type in [
        (TENDER_DOC_PATH, "Tender"),
        (BID_DOC_PATH, "Bid"),
        (RULE_DOC_PATH, "Rule")
    ]:
        path = Path(path_str)
        if not path.exists():
            logger.error(f"{path_type} document not found: {path_str}")
            return False
        logger.info(f"{path_type} document found: {path_str} ({path.stat().st_size} bytes)")

    # Create agent
    logger.info(f"Creating BidReviewAgent...")
    logger.info(f"  user_id: {USER_ID}")
    logger.info(f"  project_id: {PROJECT_ID}")
    logger.info(f"  tender_doc_path: {TENDER_DOC_PATH}")
    logger.info(f"  bid_doc_path: {BID_DOC_PATH}")
    logger.info(f"  rule_doc_path: {RULE_DOC_PATH}")

    agent = BidReviewAgent(
        project_id=PROJECT_ID,
        tender_doc_path=TENDER_DOC_PATH,
        bid_doc_path=BID_DOC_PATH,
        user_id=USER_ID,
        rule_doc_path=RULE_DOC_PATH,
        logger=logger,
        max_steps=100,  # Limit steps for testing
    )

    # Initialize async components
    logger.info("Initializing agent...")
    await agent.initialize()
    logger.info("Agent initialized successfully")

    # Run review
    logger.info("Starting review process...")
    try:
        findings = await agent.run_review()
        logger.info(f"Review completed! Found {len(findings)} findings")

        for i, finding in enumerate(findings):
            logger.info(f"  Finding {i+1}: {finding.get('requirement_key', 'N/A')}")
            logger.info(f"    Compliant: {finding.get('is_compliant', 'N/A')}")
            logger.info(f"    Severity: {finding.get('severity', 'N/A')}")
            logger.info(f"    Requirement: {finding.get('requirement_content', 'N/A')[:100]}...")

    except Exception as e:
        logger.exception(f"Review failed with exception: {e}")
        return False
    finally:
        await agent.close()

    logger.info("=" * 60)
    logger.info("BidReviewAgent Validation Test Completed")
    logger.info(f"Log file: {LOG_FILE}")
    logger.info("=" * 60)

    return True


if __name__ == "__main__":
    # Check conda environment
    conda_env = os.environ.get("CONDA_DEFAULT_ENV", "unknown")
    logger.info(f"Conda environment: {conda_env}")
    logger.info(f"Python executable: {sys.executable}")

    # Run
    success = asyncio.run(main())
    sys.exit(0 if success else 1)