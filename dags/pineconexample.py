import json
from pendulum import datetime
from airflow.operators.empty import EmptyOperator
from airflow.decorators import (
    dag,
    task,
)  
from airflow.providers.pinecone.operators.pinecone import PineconeIngestOperator
from airflow.providers.pinecone.hooks.pinecone import PineconeHook
from sklearn.feature_extraction.text import TfidfVectorizer
import re
import uuid

name_for_index = "testindex"


@dag(schedule="@daily",
    start_date=datetime(2023, 1, 1),
    catchup=False,
    tags=["pinecone"],
)  # If set, this tag is shown in the DAG view of the Airflow UI
def pinecone_example_dag():
    file_path = "include/moviedata.txt"

    start = EmptyOperator(task_id="start")

    def generate_uuid5(identifier):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, '/'.join([str(i) for i in identifier])))
    
    def get_vector_dimensions(data):
        return len(data[0]['vector'][0])

    def import_data_func(text_file_path: str):
        with open(text_file_path, "r") as f:
            lines = f.readlines()
            num_skipped_lines = 0
            descriptions = []
            data = []
            for line in lines:
                parts = line.split(":::")
                title_year = parts[1].strip()
                match = re.match(r"(.+) \((\d{4})\)", title_year)
                try:
                    title, year = match.groups()
                    year = int(year)
                except:
                    num_skipped_lines += 1
                    continue
                
                genre = parts[2].strip()
                description = parts[3].strip()
                descriptions.append(description)
                data.append(
                    {
                        "movie_id": generate_uuid5(
                            identifier=[title, year, genre, description]
                            ),
                            "title": title,
                            "year": year,
                            "genre": genre,
                            "description": description,
                        }
                    )
        vectorizer = TfidfVectorizer()
        vectors = vectorizer.fit_transform(descriptions)
        for i, item in enumerate(data):
            item['vector'] = vectors[i].toarray().tolist()
        
        return data
        
    vector_data = import_data_func('include/moviedata.txt')
    vector_dims = get_vector_dimensions(vector_data)

    @task
    def createindex(vector_size: int):
        hook = PineconeHook(conn_id="pinecone")
        newindex = hook.create_index(index_name=name_for_index,
                                     dimension=vector_size)
        return newindex

    
    indexcreator = createindex(vector_size=vector_dims)

    @task
    def convert_to_pinecone_format(data):
        pinecone_data = []
        for item in data:
            if 'vector' in item and item['vector']:
                pinecone_data.append({
                    'id': item['movie_id'],
                    'values': item['vector'][0]
                    })
        return pinecone_data
    
    data_conversion = convert_to_pinecone_format(vector_data)


    ingestion = PineconeIngestOperator(task_id="pinecone_vector_ingest",
                                       conn_id="pinecone",
                                       index_name=name_for_index,
                                       input_vectors=data_conversion,)
    
    @task
    def query_pinecone(vector_data: list):
        hook = PineconeHook(conn_id="pinecone")

        query_vector = vector_data[0]['values']
        query_response = hook.query_vector(
            index_name=name_for_index,
            top_k=10,
            include_values=True,
            include_metadata=True,
            vector=query_vector
            )
        print(query_response)
    
    querytask = query_pinecone(vector_data=data_conversion)

    @task
    def delete_pinecone_index():
        hook = PineconeHook(conn_id="pinecone")
        deletion = hook.delete_index(index_name=name_for_index)

    clean_up_task = delete_pinecone_index()
    clean_up_task.as_teardown(setups=[indexcreator])

    
    start >> indexcreator >> data_conversion >> ingestion >> querytask >> clean_up_task



pinecone_example_dag()
