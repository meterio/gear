LOGGING_CONFIG = { 
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': { 
        'standard': { 
            'format': '%(asctime)s [%(levelname)s] %(message)s'
        },
    },
    'handlers': { 
        'default': { 
            'level': 'INFO',
            'formatter': 'standard',
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',  # Default is stderr
        },
    },
    'loggers': { 
        '': {  # root logger
            'handlers': ['default'],
            'level': 'INFO',
            'propagate': False
        },
        'aiohttp.access': { 
            'handlers': ['default'],
            'level': 'INFO',
            'propagate': False
        },
        'aiohttp.web': { 
            'handlers': ['default'],
            'level': 'INFO',
            'propagate': False
        },
        'aiohttp.client': { 
            'handlers': ['default'],
            'level': 'INFO',
            'propagate': False
        },
        'aiohttp.server': { 
            'handlers': ['default'],
            'level': 'INFO',
            'propagate': False
        },
        '__main__': {  # if __name__ == '__main__'
            'handlers': ['default'],
            'level': 'DEBUG',
            'propagate': False
        },
    },
}