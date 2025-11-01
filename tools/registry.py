
from typing import Dict, Type, Optional
from pydantic import BaseModel, create_model

class ConfigRegistry:
    """Independent registry enabling drag-and-drop tool functionality without circular dependencies."""
    
    _registry: Dict[str, Type[BaseModel]] = {}
    
    @classmethod
    def register(cls, name: str, config_class: Type[BaseModel]) -> None:
        cls._registry[name] = config_class
    
    @classmethod
    def get(cls, name: str) -> Optional[Type[BaseModel]]:
        return cls._registry.get(name)
    
    @classmethod
    def create_default(cls, name: str) -> Type[BaseModel]:
        """Creates default config class with enabled=True for unregistered tools."""
        class_name = f"{name.capitalize()}Config"
        if name.endswith('_tool'):
            parts = name.split('_')
            class_name = ''.join(part.capitalize() for part in parts[:-1]) + 'ToolConfig'
        
        default_class = create_model(
            class_name,
            __base__=BaseModel,
            enabled=(bool, True),
            __doc__=f"Default configuration for {name}"
        )
        
        cls.register(name, default_class)
        
        return default_class
    
    @classmethod
    def get_or_create(cls, name: str) -> Type[BaseModel]:
        config_class = cls.get(name)
        if config_class is None:
            config_class = cls.create_default(name)
        return config_class
    
    @classmethod
    def list_registered(cls) -> Dict[str, str]:
        return {name: config_class.__name__ for name, config_class in cls._registry.items()}

registry = ConfigRegistry()