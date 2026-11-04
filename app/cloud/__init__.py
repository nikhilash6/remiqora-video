"""Where the models run (docs: «Модели и настройки» → «Где работают модели»):

- local   - this machine's GPU, as always (app/engine/worker.py, the writer's llama-server here);
- cloud   - a server the studio rents itself from a provider the user picked (providers/), with the user's own API key:
            the studio creates it, fetches the models onto its disk, connects, starts it for a shoot and stops it when
            idle (manager.py);
- server  - the user's own server (cloud/worker_server.py anywhere): its address and token by hand.

cloud and server both mean ENGINE=remote for the engine (app/engine/remote.py) and the writer (app/llm/client.py): only
the address and the token differ. The projects, the montage, RTX Video and RIFE stay on this machine in every mode.
"""
