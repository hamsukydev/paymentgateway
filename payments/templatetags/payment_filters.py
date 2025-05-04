from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Template filter to get an item from a dictionary by key
    Usage: {{ my_dict|get_item:key_var }}
    """
    if dictionary is None:
        return None
    
    return dictionary.get(key)

@register.filter(name='index')
def index(list_obj, index_value):
    """
    Template filter to get an item from a list by index
    Usage: {{ my_list|index:position }}
    """
    if list_obj is None:
        return None
    
    try:
        return list_obj[index_value]
    except (IndexError, TypeError):
        return None