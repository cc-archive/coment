from setuptools import setup

setup(
    name = "cc_coment",
    packages = ['cc_coment', 'coment'],
    package_dir = {'':'src'},

    entry_points = {
        
        'console_scripts' : [
            'django = cc_coment.scripts:manage',
            'coment.fcgi = cc_coment.scripts:fcgi'
            ]
        },
    
    install_requires = ['setuptools',
                        'Django',
                        'flup',
                        ],

    include_package_data = True,
    zip_safe = True,

    author = 'Nathan R. Yergler',
    author_email = 'nathan@creativecommons.org',
    description = '',
    license = 'AGPL 3.0',

    )
