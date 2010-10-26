# LittleChef's setup.py
from distutils.core import setup
setup(
    name = "littlechef",
    version = "0.1",
    description = "Cook with Chef without Chef Server",
    author = "Miquel Torres",
    author_email = "tobami@googlemail.com",
    url = "http://github.com/tobami/littlechef",
    download_url = "http://github.com/tobami/littlechef/archives/master",
    keywords = ["chef", "devops"],
    install_requires=['fabric>=0.9.2'],
    data_files=[
        ('littlechef/roles', ['roles/loadbalancer.json']),
        ('littlechef/cookbooks/haproxy', [
            'cookbooks/haproxy/README.rdoc', 'cookbooks/haproxy/metadata.json',
            'cookbooks/haproxy/metadata.rb']),
        ('littlechef/cookbooks/haproxy/attributes', [
            'cookbooks/haproxy/attributes/default.rb']),
        ('littlechef/cookbooks/haproxy/recipes', [
            'cookbooks/haproxy/recipes/default.rb']),
        ('littlechef/cookbooks/haproxy/templates/default', [
            'cookbooks/haproxy/templates/default/haproxy-default.erb',
            'cookbooks/haproxy/templates/default/haproxy.cfg.erb']),
    ],
    py_modules = ['littlechef'],
    scripts = ['cook'],
    classifiers = [
        "Programming Language :: Python",
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        'Topic :: System :: Systems Administration',
        ],
    long_description = """\
Cook with Chef without Chef Server
-------------------------------------
It works as follows: Whenever you apply a recipe to a node, your cookbook dir is gzipped and uploaded to that node. A node.json file gets created on the fly and uploaded, and Chef Solo gets executed at the remote node, using node.json as the node configuration and the pre-installed solo.rb for Chef Solo configuration. Cookbooks and roles are configured to be found at (/tmp/chef-solo/).

The result is that you can play as often with your recipes and nodes as you want, without having to worry about repositories, central servers nor anything else. Once you are satisfied with a new feature in a cookbook, you can commit the littlechef/cookbook/ directory to your repository. LittleChef brings back sanity to cookbook development.
"""
)
