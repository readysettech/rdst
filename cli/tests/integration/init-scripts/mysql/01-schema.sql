-- MySQL schema for RDST integration tests
-- IMDb-like test dataset with title_basics and title_ratings tables

-- Drop existing tables if they exist
DROP TABLE IF EXISTS title_ratings;
DROP TABLE IF EXISTS title_basics;

-- Create title_basics table
CREATE TABLE title_basics (
    tconst VARCHAR(255) NOT NULL PRIMARY KEY,
    titletype VARCHAR(255),
    primarytitle VARCHAR(255),
    originaltitle VARCHAR(255),
    isadult TINYINT(1),
    startyear INT,
    endyear INT,
    runtimeminutes INT,
    genres VARCHAR(255),
    INDEX idx_titletype (titletype),
    INDEX idx_startyear (startyear),
    INDEX idx_genres (genres(100))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Create title_ratings table
CREATE TABLE title_ratings (
    tconst VARCHAR(255) NOT NULL PRIMARY KEY,
    averagerating DECIMAL(3,1),
    numvotes INT,
    INDEX idx_averagerating (averagerating),
    INDEX idx_numvotes (numvotes),
    FOREIGN KEY (tconst) REFERENCES title_basics(tconst)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
