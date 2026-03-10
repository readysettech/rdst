-- PostgreSQL schema for RDST integration tests
-- IMDb-like test dataset with title_basics and title_ratings tables

-- Enable pg_stat_statements for query analysis tests
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Drop existing tables if they exist
DROP TABLE IF EXISTS title_ratings;
DROP TABLE IF EXISTS title_basics;

-- Create title_basics table
CREATE TABLE title_basics (
    tconst TEXT NOT NULL PRIMARY KEY,
    titletype TEXT,
    primarytitle TEXT,
    originaltitle TEXT,
    isadult BOOLEAN,
    startyear INTEGER,
    endyear INTEGER,
    runtimeminutes INTEGER,
    genres TEXT
);

-- Create title_ratings table
CREATE TABLE title_ratings (
    tconst TEXT NOT NULL PRIMARY KEY,
    averagerating NUMERIC(3,1),
    numvotes INTEGER,
    FOREIGN KEY (tconst) REFERENCES title_basics(tconst)
);

-- Create indexes for common query patterns
CREATE INDEX idx_title_basics_titletype ON title_basics(titletype);
CREATE INDEX idx_title_basics_startyear ON title_basics(startyear);
CREATE INDEX idx_title_basics_genres ON title_basics(genres);
CREATE INDEX idx_title_ratings_averagerating ON title_ratings(averagerating);
CREATE INDEX idx_title_ratings_numvotes ON title_ratings(numvotes);
