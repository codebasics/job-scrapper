# Role Normalization Utility - EMD Architecture
# Maps raw job roles to standardized categories using patterns

import json
import re
from typing import Dict, List

class RoleNormalizer:
    """Normalizes job roles using pattern matching from reference file"""
    
    def __init__(self, reference_file: str = "src/config/roles_reference_2026.json"):
        self.reference_file = reference_file
        self.role_patterns: Dict[str, List[re.Pattern[str]]] = {}
        self._load_patterns()
    
    def _load_patterns(self) -> None:
        """Load and compile regex patterns from reference file"""
        try:
            with open(self.reference_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for role_config in data.get('roles', []):
                name = role_config['name']
                patterns = [
                    re.compile(pattern, re.IGNORECASE) 
                    for pattern in role_config['patterns']
                ]
                self.role_patterns[name] = patterns
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load role reference file: {e}")
            self.role_patterns = {}
    
    def normalize_role(self, raw_role: str) -> str:
        """
        Normalize a raw role to standardized category
        Returns the category name or 'Other' if no match
        """
        if not raw_role or not self.role_patterns:
            return "Other"
        
        # Try to match against each category's patterns
        for category, patterns in self.role_patterns.items():
            for pattern in patterns:
                if pattern.search(raw_role):
                    return category
        
        return "Other"
    
    def get_all_categories(self) -> List[str]:
        """Get list of all available role categories"""
        return sorted(self.role_patterns.keys())
