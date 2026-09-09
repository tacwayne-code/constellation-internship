import uvicorn
from lighthouse.settings import Settings
from lighthouse.app import create_app

if __name__ == '__main__':
    settings = Settings.from_env()
    # One collector; do not start multiple web workers.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
