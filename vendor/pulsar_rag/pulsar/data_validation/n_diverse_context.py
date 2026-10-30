import datetime
import logging
import os
from functools import lru_cache
from io import StringIO
import boto3
import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer
import faiss

os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
s3 = boto3.client('s3')
logger = logging.getLogger()
logger.setLevel(logging.INFO)

@lru_cache(maxsize=1)
def _default_model():
    """Loaded on first use: importing this module (prompt_validator does, for file_transfer) no longer loads a model
    or exits the process when the load fails."""
    return SentenceTransformer('paraphrase-MiniLM-L6-v2')


def encode_sentences(sentences, batch_size=32, vector_model=None):
    # vector_model was ignored: block_diverse_data passed all-mpnet-base-v2 and got MiniLM vectors
    return (vector_model or _default_model()).encode(sentences, batch_size=batch_size, show_progress_bar=True)


def build_faiss_index(embeddings):
    d = embeddings.shape[1]  # dimension of embeddings
    index = faiss.IndexFlatIP(d)  # Inner Product similarity (cosine similarity for normalized vectors)
    faiss.normalize_L2(embeddings)  # Normalize embeddings for cosine similarity
    index.add(embeddings)
    return index


def select_diverse_samples(df, embeddings, num_samples=300, similarity_threshold=0.7):
    """Greedy: keep the newest (last) remaining row, drop every remaining row more similar to it than the threshold, repeat.
    Each step compares the kept row with all rows at once (the old search returned only as many hits as rows were
    left, so near duplicates ranked behind already-dropped rows were missed, and its list filtering was quadratic)."""
    total_samples = len(df)
    num_samples = min(num_samples, total_samples)
    if total_samples == 0:
        return df
    vectors = np.array(embeddings, dtype="float32")
    faiss.normalize_L2(vectors)
    remaining = np.ones(total_samples, dtype=bool)
    selected_indices = []

    while len(selected_indices) < num_samples and remaining.any():
        centroid_idx = int(np.flatnonzero(remaining)[-1])  # rows are appended: the last is the newest
        selected_indices.append(centroid_idx)
        remaining &= vectors @ vectors[centroid_idx] <= similarity_threshold
        remaining[centroid_idx] = False

    return df.iloc[selected_indices]

def file_transfer(env, input_file):
    try:
        key = datetime.datetime.now().strftime('%Y%m%d')
        s3.copy_object(
            Bucket=f"gravty-ai-{env}",
            CopySource={'Bucket': f"gravty-ai-{env}", 'Key': input_file},
            Key=f"{input_file}_{key}"
        )
        s3.delete_object(
            Bucket=f"gravty-ai-{env}", Key=input_file
        )
    except Exception as e:
        logger.warning(f"File transfer failed for {input_file} due to : {e}")

def put_diverse_context(env, program_id):
    try:
        input_file = f'{program_id}/gpt/data_store/offer/data.csv'
        obj = s3.get_object(Bucket=f"gravty-ai-{env}", Key=input_file)
        df = pd.read_csv(StringIO(obj['Body'].read().decode('utf-8')))
    except Exception as e:
        logger.info(f"Input file for diverseData had: {e}")
        raise

    logger.info(f"Total number of samples in the dataset: {len(df)}")

    sentences = df['user'].tolist()
    embeddings = encode_sentences(sentences)

    if len(df) > 5000:
        blockwise_store = select_diverse_samples(df, embeddings, num_samples=1000, similarity_threshold=0.7)
        file_transfer(env=env, input_file=input_file)
        blockwise_store_buffer = StringIO()
        blockwise_store.to_csv(blockwise_store_buffer, index=False)
        blockwise_store_content = blockwise_store_buffer.getvalue()
        s3.put_object(Body=blockwise_store_content, Bucket=f"gravty-ai-{env}",
                      Key=input_file)

    rag_samples = select_diverse_samples(df, embeddings, num_samples=800, similarity_threshold=0.9)
    remaining_df = df.drop(rag_samples.index)

    offer_samples = pd.DataFrame()
    if datetime.datetime.now().day == 1:
        remaining_embeddings = encode_sentences(remaining_df['user'].tolist())
        offer_samples = select_diverse_samples(remaining_df, remaining_embeddings, num_samples=25,
                                               similarity_threshold=0.9)
    try:
        rag_csv_buffer = StringIO()
        rag_samples.to_csv(rag_csv_buffer, index=False)
        rag_csv_content = rag_csv_buffer.getvalue()
        s3.put_object(Body=rag_csv_content, Bucket=f"gravty-ai-{env}",
                      Key=f"{program_id}/gpt/rag/offer/vectordb/data.csv")
        logger.info(f"Successfully wrote {len(rag_samples)} unique samples to RAG S3")

        if not offer_samples.empty:
            offer_csv_buffer = StringIO()
            offer_samples.to_csv(offer_csv_buffer, index=False)
            offer_csv_content = offer_csv_buffer.getvalue()
            s3.put_object(Body=offer_csv_content, Bucket=f"gravty-ai-{env}",
                          Key=f"{program_id}/gpt/data_store/context/data.csv")
            logger.info(f"Successfully wrote {len(offer_samples)} unique samples to Offer S3")
    except Exception as e:
        logger.warning(f"Error writing output to S3: {e}")

    logger.info(f"Selected a total of {len(rag_samples)} unique samples for RAG and {len(offer_samples)} for Offer.")