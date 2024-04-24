# jargon_rag

An experiment in Retrieval Augmented Generation, with the aim of allowing an LLM to effectively retrieve notes from my personal Obsidian vault. The ingest script assumes that the structure of the vault is that from TrustedSec's [Taming a Collective Consciousness](https://trustedsec.com/blog/obsidian-taming-a-collective-consciousness) blog post (very good read).

Notes from the Obsidian vault are uploaded to a Neo4J graph database with a vector search index. Embeddings for each note are included as an attribute for each node in the graph, meaning that semantic search can be performed across all nodes. Edges in the graph are added for all links between notes.

## The Problem

Most RAG pipelines are variants on semantic search, where embeddings are generated for documents and stored in a vector datastore, and then at inference time, an embedding is generated for the user's query, and we compare how similar that embedding is to all the document embeddings. Unfortunately, technical jargon tends to lose its meaning in this process, as vocabularies aren't large enough to have tokens dedicated to our nonsense words and acronyms. As a result, even "S3" (a popular storage solution in AWS) gets split into ["S","3"] during tokenisation. This massively changes the meaning, and renders semantic search pointless.

## The ~Solution~ Bodge

Drag the net wider to help narrow in on the documents that you want. In particular, I'm interested in technical jargon, and want to run keyword search on any jargon terms. I define jargon as any word with a high token / character ratio, and perform a text search on any that is found in the user's query.

The general outline for this RAG pipeline is as follows:

1. Embed the user's query, and run semantic search on it - you may get lucky.
2. Find jargon terms (`num_tokens / num_chars > 0.5`) and perform a text search for them.
3. This typically gets to the right area of the knowledge graph, but not the exact right notes. Find all notes that are linked to by the nodes we've already identified in the Neo4J graph.
4. After that, we have a big list of useful and useless notes. Ask the LLM which ones would be useful to answer the query. (Do this a few times and take a maximal set - 4-bit quantized 8B models aren't very reliable)
5. Take a maximal set of what the LLM asked for. Sort them semantically, and drop as many as you need to fit into the context window. This is your RAG context.
6. Actually ask the model the user's query with the context you've found.

## Does it work?

Is it reliable? No. I don't think it's even got a single 9 of reliability.

Is it better than other RAG pipelines? That I've tried, yes.

How could you improve it? Better models would go a long way. The main point it falls over is step 4, which I think is due to the size of model I'm experimenting with. If anybody wants to donate a couple of 24GB cards, I'd be happy to get data on how 70B models work with the pipeline.
