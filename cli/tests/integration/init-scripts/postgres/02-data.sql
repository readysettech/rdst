-- PostgreSQL test data for RDST integration tests
-- Subset of IMDb data sufficient for testing

-- Insert title_basics data
INSERT INTO title_basics (tconst, titletype, primarytitle, originaltitle, isadult, startyear, endyear, runtimeminutes, genres) VALUES
('tt0000001', 'short', 'Carmencita', 'Carmencita', false, 1894, NULL, 1, 'Documentary,Short'),
('tt0000002', 'short', 'Le clown et ses chiens', 'Le clown et ses chiens', false, 1892, NULL, 5, 'Animation,Short'),
('tt0000003', 'short', 'Pauvre Pierrot', 'Pauvre Pierrot', false, 1892, NULL, 4, 'Animation,Comedy,Romance'),
('tt0000004', 'short', 'Un bon bock', 'Un bon bock', false, 1892, NULL, 12, 'Animation,Short'),
('tt0000005', 'short', 'Blacksmith Scene', 'Blacksmith Scene', false, 1893, NULL, 1, 'Comedy,Short'),
('tt0000006', 'short', 'Chinese Opium Den', 'Chinese Opium Den', false, 1894, NULL, 1, 'Short'),
('tt0000007', 'short', 'Corbett and Courtney Before the Kinetograph', 'Corbett and Courtney Before the Kinetograph', false, 1894, NULL, 1, 'Short,Sport'),
('tt0000008', 'short', 'Edison Kinetoscopic Record of a Sneeze', 'Edison Kinetoscopic Record of a Sneeze', false, 1894, NULL, 1, 'Documentary,Short'),
('tt0000009', 'movie', 'Miss Jerry', 'Miss Jerry', false, 1894, NULL, 45, 'Romance'),
('tt0000010', 'short', 'Leaving the Factory', 'La sortie de l''usine Lumière à Lyon', false, 1895, NULL, 1, 'Documentary,Short'),
('tt0000011', 'short', 'Akrobatisches Potpourri', 'Akrobatisches Potpourri', false, 1895, NULL, 1, 'Documentary,Short'),
('tt0000012', 'short', 'The Arrival of a Train', 'L''arrivée d''un train à La Ciotat', false, 1896, NULL, 1, 'Documentary,Short'),
('tt0000013', 'short', 'The Photographical Congress Arrives in Lyon', 'Le débarquement du congrès de photographie à Lyon', false, 1895, NULL, 1, 'Documentary,Short'),
('tt0000014', 'short', 'The Sprinkler Sprinkled', 'L''arroseur arrosé', false, 1895, NULL, 1, 'Comedy,Short'),
('tt0000015', 'short', 'Autour d''une cabine', 'Autour d''une cabine', false, 1894, NULL, 2, 'Animation,Short'),
('tt0000016', 'short', 'Boat Leaving the Port', 'Barque sortant du port', false, 1895, NULL, 1, 'Documentary,Short'),
('tt0000017', 'short', 'Baignade en mer', 'Baignade en mer', false, 1895, NULL, 1, 'Short'),
('tt0000018', 'short', 'Das boxende Känguruh', 'Das boxende Känguruh', false, 1895, NULL, 1, 'Short'),
('tt0000019', 'short', 'The Clown Barber', 'Le barbier fin de siècle', false, 1895, NULL, 1, 'Comedy,Short'),
('tt0000020', 'short', 'The Derby 1895', 'The Derby 1895', false, 1895, NULL, 1, 'Documentary,Short,Sport'),
('tt0000021', 'short', 'Conjurer Making Ten Hats in Sixty Seconds', 'Chapellerie et charcuterie mécaniques', false, 1896, NULL, 1, 'Short'),
('tt0000022', 'short', 'Blacksmith Scene', 'Les forgerons', false, 1895, NULL, 1, 'Documentary,Short'),
('tt0000023', 'short', 'The Sea', 'La mer', false, 1895, NULL, 1, 'Documentary,Short'),
('tt0000024', 'short', 'Opening of the Kiel Canal', 'Opening of the Kiel Canal', false, 1895, NULL, 4, 'Documentary,News,Short'),
('tt0000025', 'short', 'The Oxford and Cambridge University Boat Race', 'The Oxford and Cambridge University Boat Race', false, 1895, NULL, 1, 'News,Short,Sport'),
-- Add more recent movies for startyear filtering tests
('tt0100001', 'movie', 'Test Movie 2019', 'Test Movie 2019', false, 2019, NULL, 120, 'Drama'),
('tt0100002', 'movie', 'Test Movie 2020', 'Test Movie 2020', false, 2020, NULL, 110, 'Action'),
('tt0100003', 'movie', 'Test Movie 2021', 'Test Movie 2021', false, 2021, NULL, 130, 'Comedy'),
('tt0100004', 'movie', 'Test Movie 2022', 'Test Movie 2022', false, 2022, NULL, 95, 'Drama,Romance'),
('tt0100005', 'movie', 'Test Movie 2023', 'Test Movie 2023', false, 2023, NULL, 105, 'Action,Thriller'),
-- Add variety of title types
('tt0200001', 'tvSeries', 'Test TV Series', 'Test TV Series', false, 2020, 2023, 45, 'Drama'),
('tt0200002', 'tvMovie', 'Test TV Movie', 'Test TV Movie', false, 2021, NULL, 90, 'Comedy'),
('tt0200003', 'tvEpisode', 'Test Episode', 'Test Episode', false, 2022, NULL, 42, 'Drama'),
('tt0200004', 'videoGame', 'Test Video Game', 'Test Video Game', false, 2023, NULL, NULL, 'Action,Adventure'),
('tt0200005', 'tvMiniSeries', 'Test Mini Series', 'Test Mini Series', false, 2022, 2022, 55, 'Mystery,Thriller'),
-- Add movies with specific genres for scan tests
('tt0300001', 'movie', 'Action Drama Test', 'Action Drama Test', false, 2020, NULL, 140, 'Action,Drama'),
('tt0300002', 'movie', 'Comedy Romance Test', 'Comedy Romance Test', false, 2021, NULL, 100, 'Comedy,Romance'),
('tt0300003', 'movie', 'Horror Thriller Test', 'Horror Thriller Test', false, 2019, NULL, 95, 'Horror,Thriller'),
('tt0300004', 'movie', 'Sci-Fi Adventure Test', 'Sci-Fi Adventure Test', false, 2022, NULL, 150, 'Sci-Fi,Adventure'),
('tt0300005', 'movie', 'Documentary Test', 'Documentary Test', false, 2023, NULL, 85, 'Documentary');

-- Insert title_ratings data
INSERT INTO title_ratings (tconst, averagerating, numvotes) VALUES
('tt0000001', 5.7, 1945),
('tt0000002', 6.0, 263),
('tt0000003', 6.5, 1761),
('tt0000004', 5.4, 176),
('tt0000005', 6.2, 2540),
('tt0000006', 5.1, 179),
('tt0000007', 5.2, 829),
('tt0000008', 5.3, 2035),
('tt0000009', 5.4, 218),
('tt0000010', 7.4, 7478),
('tt0000011', 5.2, 360),
('tt0000012', 7.3, 12457),
('tt0000013', 5.8, 305),
('tt0000014', 7.0, 7836),
('tt0000015', 6.1, 1285),
('tt0000016', 6.2, 1217),
('tt0000017', 5.6, 679),
('tt0000018', 5.3, 372),
('tt0000019', 5.5, 281),
('tt0000020', 5.3, 346),
('tt0000021', 5.4, 309),
('tt0000022', 6.2, 1049),
('tt0000023', 5.7, 453),
('tt0000024', 5.2, 145),
('tt0000025', 5.4, 163),
('tt0100001', 7.5, 15000),
('tt0100002', 8.0, 25000),
('tt0100003', 6.8, 12000),
('tt0100004', 7.2, 18000),
('tt0100005', 8.5, 50000),
('tt0200001', 8.2, 100000),
('tt0200002', 6.5, 8000),
('tt0200003', 7.8, 5000),
('tt0200004', 8.8, 200000),
('tt0200005', 9.0, 150000),
('tt0300001', 7.0, 20000),
('tt0300002', 6.9, 15000),
('tt0300003', 7.5, 30000),
('tt0300004', 8.3, 45000),
('tt0300005', 7.8, 10000);

-- Verify data loaded
DO $$
BEGIN
    RAISE NOTICE 'Loaded % rows into title_basics', (SELECT COUNT(*) FROM title_basics);
    RAISE NOTICE 'Loaded % rows into title_ratings', (SELECT COUNT(*) FROM title_ratings);
END $$;
