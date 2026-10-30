import os
from pathlib import Path
import faiss
import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from collections import defaultdict
from jsonl_creator import json_creator
from pulsar.data_validation.n_diverse_context import encode_sentences
from test_train_dataset import split_jsonl
from general.loggerutils import get_logger
import defusedxml.ElementTree as ET

logger = get_logger("GRAVTY_AI")
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

# Load the pre-trained Sentence-BERT model
try:
    model = SentenceTransformer('all-mpnet-base-v2')
except Exception as e:
    logger.info(f"Error loading the model: {e}")
    exit(1)
base_dir = Path.home() / "Desktop"
operators = ['==', '>=', '<=', 'not_null', 'null', '!=']

def compute_cluster_scores(k, embeddings):
    """Silhouette of a k-means clustering (-1..1, higher is better). The old score averaged it with Calinski-Harabasz,
    which is unbounded (hundreds to thousands) and so decided alone, and with 1/(-Davies-Bouldin)."""
    logger.info(f"Computing clusters for k={k}")
    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = kmeans.fit_predict(embeddings)
    return silhouette_score(embeddings, labels)


def find_optimal_clusters(embeddings, max_clusters=60, min_clusters=7):
    """The k in [min_clusters, max_clusters] with the best silhouette, kept inside 2..n-1 (what k-means and the
    silhouette accept: the old floor of 7 asked a 3-sample cluster for 7 clusters, and k-means raised). A coarse scan,
    then every k around the best: the score is not unimodal in k, so a binary search could stop on a worse k."""
    n = len(embeddings)
    if n < 3:
        return 1
    hi = max(2, min(max_clusters, n - 1))
    lo = max(2, min(min_clusters, hi))
    step = max(1, (hi - lo) // 8)
    scores = {}

    def score(k):
        if k not in scores:
            scores[k] = compute_cluster_scores(k, embeddings)
        return scores[k]

    best_k = max(range(lo, hi + 1, step), key=score)
    return max(range(max(lo, best_k - step + 1), min(hi, best_k + step - 1) + 1), key=score, default=best_k)



def hierarchical_clustering(df, max_main_clusters=40,
                            max_sub_clusters=40):
    sentences = df.iloc[:, 0].tolist()
    embeddings = encode_sentences(sentences=sentences, vector_model=model)

    # Find optimal number of main clusters
    n_main_clusters = find_optimal_clusters(embeddings, max_clusters=max_main_clusters)
    logger.info(f"Optimal number of main clusters: {n_main_clusters}")

    # First level of clustering
    main_kmeans = KMeans(n_clusters=n_main_clusters, random_state=42, n_init=10)
    main_labels = main_kmeans.fit_predict(embeddings)

    categories = defaultdict(lambda: defaultdict(list))

    for main_label in range(n_main_clusters):
        main_cluster_mask = (main_labels == main_label)
        main_cluster_embeddings = embeddings[main_cluster_mask]

        if len(main_cluster_embeddings) > 2:  # Ensure we have enough samples for sub-clustering
            # Find optimal number of sub-clusters
            n_sub_clusters = find_optimal_clusters(main_cluster_embeddings,
                                                   max_clusters=min(max_sub_clusters, len(main_cluster_embeddings) - 1))
            logger.info(f"Optimal number of sub-clusters for main cluster {main_label}: {n_sub_clusters}")

            # Second level of clustering
            sub_kmeans = KMeans(n_clusters=n_sub_clusters, random_state=42, n_init=10)
            sub_labels = sub_kmeans.fit_predict(main_cluster_embeddings)

            for sub_label in range(n_sub_clusters):
                sub_cluster_mask = (sub_labels == sub_label)
                sub_cluster_indices = np.where(main_cluster_mask)[0][sub_cluster_mask]
                categories[f'main_cluster_{main_label}'][f'sub_cluster_{sub_label}'] = sub_cluster_indices.tolist()
        else:
            # If not enough samples for sub-clustering, keep all in one sub-cluster
            categories[f'main_cluster_{main_label}'][f'sub_cluster_0'] = np.where(main_cluster_mask)[0].tolist()

    return categories


def encode_sentences_in_batches(sentences, batch_size=5000):
    embeddings_list = []
    for i in range(0, len(sentences), batch_size):
        batch = sentences[i:i + batch_size]
        batch_embeddings = encode_sentences(sentences=batch, batch_size=16, vector_model=model)
        embeddings_list.append(batch_embeddings)
    return np.vstack(embeddings_list)


def build_faiss_index_in_batches(embeddings, batch_size=5000):
    d = embeddings.shape[1]
    index = faiss.IndexFlatIP(d)

    def normalize_embeddings(embeddings, batch_size=5000):
        for i in range(0, embeddings.shape[0], batch_size):
            faiss.normalize_L2(embeddings[i: i + batch_size])
        return embeddings

    embeddings = normalize_embeddings(embeddings)

    for i in range(0, len(embeddings), batch_size):
        batch = embeddings[i:i + batch_size]
        index.add(batch)

    return index


def farthest_points(distances, start, n):
    """n rows, each the farthest from those already picked (max-min), starting at `start`."""
    picked, nearest = [start], distances[start].astype(float)
    nearest[start] = -1.0  # never picked twice, even among identical rows
    while len(picked) < min(n, len(distances)):
        nxt = int(np.argmax(nearest))
        picked.append(nxt)
        nearest = np.minimum(nearest, distances[nxt])
        nearest[picked] = -1.0
    return np.array(picked)


def select_diverse_finetuning_samples(df, min_samples_per_length=10):
    df = df.copy()  # Avoid modifying original
    df['length'] = df.iloc[:, 0].apply(lambda x: len(str(x).split()))

    print(f"Original length distribution:\n{df['length'].value_counts()}\n")

    df_sorted = df.sort_values(by='length', ascending=False)
    selected_indices = []
    length_groups = df_sorted.groupby('length')

    for length, group in length_groups:
        print(f"\nProcessing length {length} (group size: {len(group)})")

        if len(group) < min_samples_per_length:
            selected_samples = group.index.tolist()
            print(f"Group smaller than min_samples_per_length, taking all {len(selected_samples)} samples")
        else:
            # Step 1: Convert the text (or other feature) within the group to embeddings
            group_text = group.iloc[:, 0]  # Assuming text is in the first column
            group_embeddings = encode_sentences_in_batches(group_text.tolist())  # Convert Series to List

            # Step 2: Normalize embeddings for better distance computation
            group_embeddings_normalized = group_embeddings.copy()
            faiss.normalize_L2(group_embeddings_normalized)

            # Step 3: squared L2 distance between every pair, row i = sample i in group order (unit vectors:
            # 2 - 2 cos). A FAISS search returns each row sorted by distance, so its diagonal is not self: the old
            # fill_diagonal put inf into one real neighbour of every row, every mean became inf, and the "farthest"
            # pick was simply the last rows of the group.
            distances = np.maximum(0.0, 2.0 - 2.0 * group_embeddings_normalized @ group_embeddings_normalized.T)

            # Step 4: Select the min_samples_per_length points farthest, on average, from the others
            mean_distances = (distances.sum(axis=1) - np.diag(distances)) / (len(distances) - 1)
            farthest_indices = farthest_points(distances, int(np.argmax(mean_distances)), min_samples_per_length)
            selected_samples = group.index[farthest_indices].tolist()

            print(f"Selected {len(selected_samples)} diverse samples")

        selected_indices.extend(selected_samples)

    result = df.loc[selected_indices].drop('length', axis=1)  # Drop the 'length' column used for grouping
    print(f"\nFinal length distribution:\n{result.iloc[:, 0].apply(len).value_counts()}")
    print(f"Total selected samples: {len(result)}")

    return result


def usable_lines(df_csv):
    """Rows whose first column is a complete line: not empty or "na", over 10 characters and 4 words, not ending in an
    operator (a condition with no value). str[-1] (one character) never equalled the operators ('==', '>=', ...)."""
    col = df_csv[df_csv.columns[0]]
    return df_csv[col.notna() & (col.str.strip().str.lower() != "na") & (col.str.strip().str.len() > 10)
                  & (col.str.split().str.len() >= 4)
                  & (~col.str.strip().str.endswith(tuple(operators)))].reset_index(drop=True)


def main(df_csv=None, file_name: str = 'block'):
    """Input file key"""
    system_context_block = (
        "You are an AI assistant specializing in interpreting block-based programming data for "
    "loyalty systems. Your primary task is to analyze and explain entries consisting of two "
    "parts: a SubPrompt, which is a text description outlining an action to be performed ("
    "such as updating attributes or calculating values), and a Blocks section, which contains "
    "structured elements defining or executing the described action. The output should "
    "strictly follow the sequence of elements as specified in the input XML, recognizing that "
    "the sequence may vary across different data points. Additionally, ensure that the "
    "generated XML syntax is accurate, with all tags properly opened and closed, adhering to "
    "standard XML formatting rules. The responses must be strictly aligned with the provided "
    "data, avoiding hallucinations or any deviations from the input content. These principles "
    "are critical for maintaining accuracy, consistency, and usability within the loyalty "
    "systems framework."
    )
    system_context_mutation = (
        "You are an AI assistant specialized in parsing and understanding block-based programming structures, "
        "particularly those involving mutations and activity attributes. Your task is to analyze and explain the relationship "
        "between mutation XML and corresponding block XML in visual programming environments."
    )
    try:
        if df_csv is None:
            raise ValueError("Data not found")
        else:
            first_column_name = df_csv.columns[0]
            df = usable_lines(df_csv)
    except FileNotFoundError:
        logger.info("CSV file not found. Please check the file path.")
        exit(1)
    system_context = system_context_block if first_column_name.lower() == "subprompt" else system_context_mutation

    logger.info(f"Total number of samples in the dataset: {len(df)}")

    categories = hierarchical_clustering(df)

    all_selected_samples = pd.DataFrame()

    for main_category, sub_categories in categories.items():
        main_category_samples = pd.DataFrame()

        for sub_category, indices in sub_categories.items():
            category_data = df.loc[indices]
            selected_indices = select_diverse_finetuning_samples(
                df=category_data
            )

            selected_samples = category_data.loc[selected_indices.index]  # Keep full data
            main_category_samples = pd.concat([main_category_samples, selected_samples])

        all_selected_samples = pd.concat([all_selected_samples, main_category_samples])


    all_selected_samples = all_selected_samples.drop_duplicates().reset_index(drop=True)
    '''Check Point for csv'''
    all_selected_samples.to_csv(f"{base_dir}/{file_name}_train.csv", index=False)
    if system_context == system_context_block:
        partition_rules = {
            "update_attribute": ['update_attribute'],
            "if_filter_block": ['if', 'offer_criteria', 'if_payment'],
            "only_priv": ['give_privilege', 'give_points', 'reset_points', 'expire_points',
                          'return_points', 'take_points'],
            "third_party": ['INSTITUTION', 'ENROLLMENT_REFERRER', 'COMPANION', 'CORPORATE',
                            'FAMILY_HEAD', 'TRANSFEREE', 'ACTIVITY_REFERRER']
        }
        partitions = partition_dataframe(all_selected_samples, partition_rules)

        # Convert each partition into JSONL files
        for key, partition in partitions.items():
            if key == "only_priv":
                partition = modify_xml_for_only_priv(partition)
                # partition.to_csv(f"{base_dir}/{key}_train.csv", index=False)

            jsonl_file_block = json_creator(input_df=partition, system_content=system_context,
                                            isMutation=first_column_name.lower() != "subprompt")
            split_jsonl(data=jsonl_file_block,
                        train_file=f"{base_dir}/{key}_train.jsonl",
                        validation_file=f"{base_dir}/{key}_validation.jsonl")


    else:
        jsonl_file_block = json_creator(input_df=all_selected_samples, system_content=system_context, isMutation=first_column_name.lower() != "subprompt")
        split_jsonl(data=jsonl_file_block, train_file=f"{base_dir}/{file_name}_train.jsonl",
                    validation_file=f"{base_dir}/{file_name}_validation.jsonl")

def partition_dataframe(df, rules):
    """Rows by the partition whose keywords occur in the SubPrompt's first word (a row may be in several). Collected,
    then built once: a concat per row copied the partition every time (quadratic)."""
    rows = {path: [] for path in rules}

    for _, row in df.iterrows():
        first_word = row['SubPrompt'].split(' ')[0] if isinstance(row['SubPrompt'], str) else ""
        for path, keywords in rules.items():
            if any(keyword in first_word for keyword in keywords):
                rows[path].append(row)

    return {path: pd.DataFrame(found, columns=df.columns).reset_index(drop=True) for path, found in rows.items()}


def modify_xml_for_only_priv(df):
    """Ensures 'PRIVILEGE_QUANTITY' comes before 'PRIVILEGE' in XML for 'only_priv' partition."""
    for index, row in df.iterrows():
        if row['SubPrompt'].split(' ')[0] == 'give_privilege':
            root = ET.fromstring(row['Block'])

            values = root.findall("value")
            privilege_index = None
            privilege_quantity_index = None

            for i, elem in enumerate(values):
                if elem.get("name") == "PRIVILEGE" and privilege_index is None:
                    privilege_index = i
                elif elem.get("name") == "PRIVILEGE_QUANTITY":
                    privilege_quantity_index = i

            if privilege_index is not None and privilege_quantity_index is not None:
                # Swap if PRIVILEGE_QUANTITY is after PRIVILEGE
                if privilege_quantity_index > privilege_index:
                    values[privilege_index], values[privilege_quantity_index] = (
                        values[privilege_quantity_index],
                        values[privilege_index],
                    )
            # elif privilege_quantity_index is None:
            #     # Create and insert new PRIVILEGE_QUANTITY block
            #     new_elem = ET.fromstring(
            #         """<value name="PRIVILEGE"><block type="static_value" id="34"><field name="value">1</field></block></value>""")
            #
            #     values.insert(0, new_elem)

            # Clear existing <value> tags
            for elem in root.findall("value"):
                root.remove(elem)

            # Append back in correct order
            for elem in values:
                root.append(elem)

            # Update DataFrame in place
            df.at[index, 'Block'] = ET.tostring(root, encoding="unicode")

    return df



if __name__ == "__main__":
    main()
