file_cache_path "%(node_work_path)s/cache"
cookbook_path %(cookbook_paths_list)s
role_path "%(node_work_path)s/roles"
data_bag_path "%(node_work_path)s/data_bags"
environment_path "%(node_work_path)s/environments"
environment "%(environment)s"
verbose_logging %(verbose)s
verify_api_cert true
