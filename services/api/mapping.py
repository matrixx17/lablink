"""
Embedding-based semantic schema mapper for lab data headers.

Uses sentence-transformers to match incoming CSV headers against
a canonical ontology of lab fields with cosine similarity.
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import yaml
from sentence_transformers import SentenceTransformer

# Configuration
SIMILARITY_THRESHOLD = 0.65
MODEL_NAME = "all-MiniLM-L6-v2"
ONTOLOGY_PATH = Path(__file__).parent.parent.parent / "ontology" / "canonical_fields.yaml"


class OntologyMapper:
    """Singleton mapper that caches ontology embeddings at startup."""

    _instance: Optional["OntologyMapper"] = None

    def __new__(cls) -> "OntologyMapper":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.model = SentenceTransformer(MODEL_NAME)
        self.canonical_fields: Dict[str, Dict] = {}
        self.field_embeddings: Dict[str, np.ndarray] = {}
        self.synonym_to_field: Dict[str, str] = {}
        self.all_synonyms: List[str] = []
        self.synonym_embeddings: Optional[np.ndarray] = None

        self._load_ontology()
        self._compute_embeddings()

    def _load_ontology(self) -> None:
        """Load canonical fields from YAML ontology file."""
        ontology_path = os.environ.get("ONTOLOGY_PATH", str(ONTOLOGY_PATH))

        with open(ontology_path, "r") as f:
            data = yaml.safe_load(f)

        self.canonical_fields = data.get("fields", {})

        # Build synonym lookup
        for field_name, field_data in self.canonical_fields.items():
            synonyms = field_data.get("synonyms", [])
            for synonym in synonyms:
                self.synonym_to_field[synonym.lower()] = field_name
                self.all_synonyms.append(synonym)

    def _compute_embeddings(self) -> None:
        """Pre-compute embeddings for all synonyms."""
        if not self.all_synonyms:
            return

        # Encode all synonyms in one batch for efficiency
        self.synonym_embeddings = self.model.encode(
            self.all_synonyms,
            convert_to_numpy=True,
            normalize_embeddings=True  # Pre-normalize for faster cosine similarity
        )

    def match_header(self, header: str) -> Tuple[str, float]:
        """
        Match a single header against the ontology.

        Returns:
            Tuple of (canonical_field_name, confidence_score)
            Returns ("unknown", 0.0) if no match above threshold
        """
        if self.synonym_embeddings is None or len(self.all_synonyms) == 0:
            return ("unknown", 0.0)

        # Encode the input header
        header_embedding = self.model.encode(
            [header],
            convert_to_numpy=True,
            normalize_embeddings=True
        )[0]

        # Compute cosine similarity (dot product since vectors are normalized)
        similarities = np.dot(self.synonym_embeddings, header_embedding)

        # Find best match
        best_idx = np.argmax(similarities)
        best_score = float(similarities[best_idx])

        if best_score < SIMILARITY_THRESHOLD:
            return ("unknown", best_score)

        best_synonym = self.all_synonyms[best_idx]
        canonical_field = self.synonym_to_field[best_synonym.lower()]

        return (canonical_field, best_score)

    def match_headers(self, headers: List[str]) -> Dict[str, Tuple[str, float]]:
        """
        Match multiple headers against the ontology.

        Returns:
            Dict mapping original header -> (canonical_field, confidence)
        """
        results = {}

        if self.synonym_embeddings is None or len(self.all_synonyms) == 0:
            return {h: ("unknown", 0.0) for h in headers}

        # Batch encode all headers for efficiency
        header_embeddings = self.model.encode(
            headers,
            convert_to_numpy=True,
            normalize_embeddings=True
        )

        # Compute all similarities at once
        # Shape: (num_headers, num_synonyms)
        all_similarities = np.dot(header_embeddings, self.synonym_embeddings.T)

        for i, header in enumerate(headers):
            similarities = all_similarities[i]
            best_idx = np.argmax(similarities)
            best_score = float(similarities[best_idx])

            if best_score < SIMILARITY_THRESHOLD:
                results[header] = ("unknown", best_score)
            else:
                best_synonym = self.all_synonyms[best_idx]
                canonical_field = self.synonym_to_field[best_synonym.lower()]
                results[header] = (canonical_field, best_score)

        return results


# Global mapper instance (initialized on first use)
_mapper: Optional[OntologyMapper] = None


def get_mapper() -> OntologyMapper:
    """Get or create the singleton mapper instance."""
    global _mapper
    if _mapper is None:
        _mapper = OntologyMapper()
    return _mapper


def guess_schema(headers: List[str]) -> Dict[str, Any]:
    """
    Map CSV headers to canonical field names using semantic similarity.

    Args:
        headers: List of column header strings from a CSV file

    Returns:
        Dict with:
            - "mapping": Dict[str, str] mapping original header -> canonical field
            - "confidence": Dict[str, float] mapping original header -> confidence score
    """
    mapper = get_mapper()
    matches = mapper.match_headers(headers)

    mapping = {h: match[0] for h, match in matches.items()}
    confidence = {h: round(match[1], 4) for h, match in matches.items()}

    return {
        "mapping": mapping,
        "confidence": confidence
    }


def match_single(header: str) -> Dict[str, Any]:
    """
    Match a single header string.

    Args:
        header: A column header string

    Returns:
        Dict with canonical_field and confidence
    """
    mapper = get_mapper()
    field, score = mapper.match_header(header)
    return {
        "header": header,
        "canonical_field": field,
        "confidence": round(score, 4)
    }


# CLI for testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python mapping.py <header> [header2] [header3] ...")
        print("Example: python mapping.py 'Temp (C)' 'OD600' 'flow rate'")
        sys.exit(1)

    headers = sys.argv[1:]

    print(f"Loading model '{MODEL_NAME}' and ontology...")
    print(f"Ontology path: {ONTOLOGY_PATH}")
    print(f"Similarity threshold: {SIMILARITY_THRESHOLD}")
    print("-" * 60)

    if len(headers) == 1:
        result = match_single(headers[0])
        print(f"Header:     {result['header']}")
        print(f"Match:      {result['canonical_field']}")
        print(f"Confidence: {result['confidence']:.4f}")
    else:
        result = guess_schema(headers)
        print(f"{'Header':<30} {'Canonical Field':<25} {'Confidence':<10}")
        print("-" * 65)
        for header in headers:
            field = result["mapping"][header]
            conf = result["confidence"][header]
            print(f"{header:<30} {field:<25} {conf:.4f}")
