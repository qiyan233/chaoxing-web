from loguru import logger
from tqdm import tqdm
import sys
from pathlib import Path

tqdm_stream = sys.stderr
log_dir = Path(__file__).resolve().parent.parent / "data"
log_dir.mkdir(parents=True, exist_ok=True)

def tqdm_sink(msg):
    tqdm.write(msg.rstrip(), file=tqdm_stream)
    tqdm_stream.flush()

logger.remove()
logger.add(tqdm_sink, colorize=True, enqueue=False)
logger.add(log_dir / "chaoxing.log", rotation="10 MB", level="TRACE", enqueue=False)
