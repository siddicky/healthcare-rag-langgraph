import asyncio
import logging
import uuid
from typing import List, Optional, Tuple, Dict, Any
import sys
import time

from ..graph.engine import Engine, build_engine
from ..orch.monitor import QueryMonitor
from ..processors.safety import scrub_phi

# Configure logging
logging.basicConfig(
    level=logging.WARNING, 
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("MedicalRAG").setLevel(logging.WARNING)

logger = logging.getLogger("MedicalRAG")

async def process_query_with_orchestrator(
    engine: Engine,
    query: str, 
    user_id: str, 
    monitor: QueryMonitor
) -> None:
    """
    Process a query using the RAG orchestrator and update the monitor with progress.
    
    Args:
        engine: The selected runtime engine
        query: The user's query
        user_id: The user ID for conversation history
        monitor: The QueryMonitor to update with results
    """
    result = await engine.process_query(query, user_id, monitor)

    monitor.final_answer = result["answer"]
    monitor.follow_up_questions = result["follow_ups"]
    monitor.final_answer_event.set()

def print_banner():
    """Print a professional banner for the application."""
    banner = """
╭───────────────────────────────────────────────╮
│                                               │
│             Medical RAG Assistant             │
│                                               │
╰───────────────────────────────────────────────╯
    """
    print(banner)

async def interactive_main():
    """Main function for interactive CLI mode."""

    print_banner()
    print("Initializing Medical RAG System...")
    try:
        engine = await build_engine()
        print("✓ Medical RAG System Initialized Successfully")
    except Exception as e:
        print(f"✗ Error initializing system: {e}")
        return

    # Use a consistent user ID for the whole session
    session_id = f"cli_user_{uuid.uuid4().hex[:8]}"
    print(f"Session ID: {session_id}")
    print("\nEnter your medical questions below. Type 'quit' to exit.")
    print("─" * 60)

    try:
        while True:
            # Get user input
            user_query = input("\n> ")
            if not user_query:
                continue
                
            if user_query.lower() in ('quit', 'exit', 'q'):
                print("\nEnding session...")
                break
                
            # Show waiting message
            print("\n🔍 Processing your question...")
            start_time = time.time()
            
            # Create a monitor for this specific query
            monitor = QueryMonitor()
            
            # Process query and show status
            status_task = asyncio.create_task(monitor.display_progress())
            process_task = asyncio.create_task(
                process_query_with_orchestrator(engine, user_query, session_id, monitor)
            )
            
            # Wait for processing to complete
            try:
                # Wait up to 30 seconds for a raw answer
                raw_answer_received = await asyncio.wait_for(
                    monitor.raw_answer_event.wait(), 
                    timeout=30.0
                )
                
                if raw_answer_received and monitor.raw_answer:
                    # Display preliminary answer. Scrubbed on the way out as a last line of
                    # defence: the gate already removed identifiers from the query, so an
                    # echo here would mean something upstream reintroduced them.
                    print("\r\033[K", end="")  # Clear the status line
                    print("\n┌─ PRELIMINARY ANSWER (not verified) ─────────────┐")
                    print(scrub_phi(monitor.raw_answer)[0])
                    print("└─────────────────────────────────────────────────┘")
                    print("🔄 Validating and refining answer...")
                    
                    # Restart status display
                    if status_task.done():
                        status_task = asyncio.create_task(monitor.display_progress())
            except asyncio.TimeoutError:
                # No raw answer within timeout, just wait for final
                pass
            
            # Now wait for the final results
            try:
                await process_task
                await status_task
                
                # Clear status line and display the final result
                print("\r\033[K", end="")
                elapsed_time = time.time() - start_time
                print(f"\n⏱️  Query processed in {elapsed_time:.2f} seconds\n")
                print("┌─" + "─" * 58 + "┐")
                if monitor.final_answer:
                    print("│ VERIFIED ANSWER:                                     │")
                    print("└─" + "─" * 58 + "┘")
                    print(scrub_phi(monitor.final_answer)[0])
                else:
                    print("│ ⚠️  Unable to find a reliable answer to your question. │")
                    print("└─" + "─" * 58 + "┘")
                
                # Show follow-up questions if available
                if monitor.follow_up_questions and len(monitor.follow_up_questions) > 0:
                    print("\n📋 Related questions you might want to ask:")
                    for i, q in enumerate(monitor.follow_up_questions, 1):
                        print(f"   {i}. {scrub_phi(q)[0]}")
                
                print("─" * 60)
                
            except Exception as e:
                print(f"\n❌ An error occurred: {e}")
                logger.error(f"Error processing query '{user_query}': {e}", exc_info=True)

    except KeyboardInterrupt:
        print("\n\nSession interrupted by user. Shutting down...")
    finally:
        print("\nClosing connection...")
        if 'engine' in locals():
            try:
                await engine.aclose()
                print("✓ Connection closed successfully.")
                print("Thank you for using Medical RAG Assistant!")
            except Exception as e:
                print(f"✗ Error while closing connection: {e}")

def main():
    """Entry point for the CLI."""
    try:
        asyncio.run(interactive_main())
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        logging.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
